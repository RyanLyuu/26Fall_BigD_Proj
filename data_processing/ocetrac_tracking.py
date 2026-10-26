"""CMEMS adapter around the released Ocetrac algorithms (MIT, v0.1.7).

Surface mode calls SurfTrack.label_3d (the 2024 paper's space-time CCL).
Volume mode calls DeepTrack.build_3d_objects/track_objects_with_splitting,
a newer upstream extension. We deliberately bypass upstream morphological
filling and default percentile/size removal: no synthetic compound voxels,
no land/missing-data bridges. Calendar validation, periodic boundaries and
explicit object-to-object lineage are application-level adaptations.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.metadata import version

import numpy as np
from scipy import ndimage
import xarray as xr

from .time_axis import tracking_blocks

DIMS = ('time', 'depth', 'latitude', 'longitude')


@dataclass(frozen=True)
class TrackingConfig:
    mode: str = 'auto'  # one depth: SurfTrack; multiple depths: DeepTrack
    periodic_longitude: bool | None = None  # auto-detect complete global grid
    min_object_cells: int = 1  # per snapshot, independent of event lifespan
    containment_threshold: float = 0.0
    volume_weight: float = 0.5


@dataclass
class TrackingResult:
    labels: np.ndarray
    objects: np.ndarray
    links: list[dict]
    metadata: dict


def is_global_longitude(longitude: np.ndarray) -> bool:
    lon = np.asarray(longitude, dtype=float)
    if lon.size < 3:
        return False
    step = np.diff(lon)
    return bool(np.allclose(step, step[0], rtol=1e-5, atol=1e-6)
                and np.isclose(lon[-1]-lon[0]+step[0], 360, atol=1e-3))


def _merge_pairs(labels: np.ndarray, pairs: np.ndarray) -> np.ndarray:
    """Union periodic duplicates without conflating zero with a real object."""
    maximum = int(labels.max(initial=0))
    parent = np.arange(maximum+1)

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in np.unique(pairs.reshape(-1, 2), axis=0):
        if a > 0 and b > 0:
            ra, rb = root(int(a)), root(int(b))
            parent[max(ra, rb)] = min(ra, rb)
    lookup = np.array([root(i) for i in range(maximum+1)])
    return lookup[labels]


def _periodic_objects(frame: np.ndarray) -> np.ndarray:
    pairs = []
    # DeepTrack uses 8-neighbour horizontal, face-only vertical connectivity.
    for dy in (-1, 0, 1):
        left = frame[:, max(0, dy):frame.shape[1]+min(0, dy), 0]
        right = frame[:, max(0, -dy):frame.shape[1]+min(0, -dy), -1]
        pairs.append(np.stack([left.ravel(), right.ravel()], axis=1))
    return _merge_pairs(frame, np.concatenate(pairs))


def _surface_labels(mask: np.ndarray, periodic: bool, label_3d) -> np.ndarray:
    result = np.zeros(mask.shape, dtype=np.int64)
    offset = 0
    for z in range(mask.shape[1]):
        data = mask[:, z]
        if periodic:
            extended = np.pad(data, ((0, 0), (0, 0), (1, 1)), mode='wrap')
            labels, _ = label_3d(xr.DataArray(extended, dims=('time', 'latitude', 'longitude')))
            pairs = np.concatenate([np.stack([labels[..., 0].ravel(), labels[..., -2].ravel()], axis=1),
                                    np.stack([labels[..., -1].ravel(), labels[..., 1].ravel()], axis=1)])
            labels = _merge_pairs(labels, pairs)[..., 1:-1]
        else:
            labels, _ = label_3d(xr.DataArray(data, dims=('time', 'latitude', 'longitude')))
        ids = np.unique(labels[labels > 0])
        for i, label in enumerate(ids, start=1):
            result[:, z][labels == label] = offset+i
        offset += len(ids)
    return result


def _lineage(objects, labels, dataset, cfg, mode, periodic, cell_volume):
    links = []
    for block in tracking_blocks(dataset):
        for t in range(block.start+1, block.stop):
            previous, current = objects[t-1], objects[t]
            candidates = set()
            if mode == 'surface':
                for parent in np.unique(previous[previous > 0]):
                    original = previous == parent
                    padded = np.pad(original, ((0, 0), (0, 0), (1, 1)), mode='wrap') if periodic else original
                    expanded = np.stack([ndimage.binary_dilation(layer, np.ones((3, 3))) for layer in padded])
                    if periodic:
                        expanded = expanded[..., 1:-1]
                    for child in np.unique(current[expanded & (current > 0)]):
                        candidates.add((int(parent), int(child)))
            else:
                overlap = (previous > 0) & (current > 0)
                candidates = set(map(tuple, np.unique(np.stack([previous[overlap], current[overlap]], axis=1), axis=0)))
            frame_links = []
            for parent, child in sorted(candidates):
                a, b = previous == parent, current == child
                common = a & b
                score = float(max(common.sum()/a.sum(), common.sum()/b.sum()))
                if cell_volume is not None:
                    physical = float(max(cell_volume[common].sum()/cell_volume[a].sum(), cell_volume[common].sum()/cell_volume[b].sum()))
                    score = (1-cfg.volume_weight)*score + cfg.volume_weight*physical
                if mode == 'volume' and score <= cfg.containment_threshold:
                    continue  # same strict comparison as upstream DeepTrack
                frame_links.append({'parent_node': f'O-{t-1:06d}-{int(parent)}',
                                    'child_node': f'O-{t:06d}-{int(child)}',
                                    'parent_label': int(labels[t-1][a][0]),
                                    'child_label': int(labels[t][b][0]),
                                    'time': np.datetime_as_string(dataset.time.values[t], unit='D'),
                                    'from_scale': str(dataset.time_scale.values[t-1]),
                                    'to_scale': str(dataset.time_scale.values[t]),
                                    'containment_score': score})
            for link in frame_links:
                split = sum(x['parent_node'] == link['parent_node'] for x in frame_links) > 1
                merge = sum(x['child_node'] == link['child_node'] for x in frame_links) > 1
                link['relation'] = 'split_merge' if split and merge else 'split' if split else 'merge' if merge else 'continuation'
                links.append(link)
    return links


def track_anomalies(mask: xr.DataArray, dataset: xr.Dataset, config: TrackingConfig | None = None,
                    cell_volume: np.ndarray | None = None) -> TrackingResult:
    cfg = config or TrackingConfig()
    if cfg.mode not in {'auto', 'surface', 'volume'}:
        raise ValueError('Tracking mode must be auto, surface or volume')
    if cfg.min_object_cells < 1 or not 0 <= cfg.containment_threshold <= 1 or not 0 <= cfg.volume_weight <= 1:
        raise ValueError('Invalid object size, containment threshold or volume weight')
    global_grid = is_global_longitude(dataset.longitude.values)
    if cfg.periodic_longitude is True and not global_grid:
        raise ValueError('Periodic longitude requires a complete, regular 360-degree grid')
    periodic = global_grid if cfg.periodic_longitude is None else cfg.periodic_longitude
    mode = ('surface' if dataset.depth.size == 1 else 'volume') if cfg.mode == 'auto' else cfg.mode
    try:
        from ocetrac.SurfTrack.tracker import label_3d
        from ocetrac.DeepTrack.grid import make_anisotropic_struct
        from ocetrac.DeepTrack.tracker import build_3d_objects, track_objects_with_splitting
    except ImportError as error:
        raise RuntimeError('Ocetrac is required: install data_processing/requirements.txt') from error
    binary = np.asarray(mask.transpose(*DIMS).fillna(False).values, dtype=bool)
    if cell_volume is not None and (cell_volume.shape != binary.shape[1:] or not np.isfinite(cell_volume).all() or (cell_volume <= 0).any()):
        raise ValueError('Cell volumes must be finite positive values on depth/latitude/longitude')
    structure = make_anisotropic_struct(connect_xy=True, connect_z=mode == 'volume')
    objects = build_3d_objects(binary, structure)
    for t, frame in enumerate(objects):
        if periodic:
            frame = _periodic_objects(frame)
        sizes = np.bincount(frame.ravel())
        keep = sizes >= cfg.min_object_cells
        keep[0] = False
        objects[t] = np.where(keep[frame], frame, 0)
    binary = objects > 0
    labels = np.zeros(binary.shape, dtype=np.int64)
    offset = 0
    for block in tracking_blocks(dataset):
        if mode == 'surface':
            tracked = _surface_labels(binary[block], periodic, label_3d)
        else:
            tracked, _ = track_objects_with_splitting(objects[block], volume_weights=cell_volume,
                                                       contain_thresh=cfg.containment_threshold,
                                                       alpha=1-cfg.volume_weight)
        labels[block] = np.where(tracked > 0, tracked + offset, 0)
        offset = int(labels[block].max(initial=offset))
    links = _lineage(objects, labels, dataset, cfg, mode, periodic, cell_volume)
    metadata = {'engine': 'ocetrac', 'version': version('ocetrac'), 'module': 'SurfTrack' if mode == 'surface' else 'DeepTrack',
                'config': asdict(cfg), 'periodic_longitude': bool(periodic),
                'morphological_filling': False, 'temporal_gap_bridging': False,
                'upstream': 'https://github.com/ocetrac/ocetrac',
                'paper_scope': 'SurfTrack: Scannell et al. 2024; DeepTrack: later upstream volumetric extension'}
    return TrackingResult(labels, objects, links, metadata)
