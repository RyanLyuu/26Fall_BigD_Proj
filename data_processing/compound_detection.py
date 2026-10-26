"""Aligned, simultaneous and sustained compound extreme-event detection.

Run ``python -m data_processing.compound_detection --help`` for the CLI.
Inputs must already share a spatial grid and depth coordinate. Native monthly
and daily intervals can be joined with data_processing.evolution.
Monthly pH is never interpolated or broadcast into a daily series.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field, replace
from itertools import combinations
import json
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
from shapely.affinity import translate
from shapely.geometry import box, mapping
from shapely.ops import unary_union
import xarray as xr

from .event_detection import (
    DetectionConfig, VARIABLE_RULES, _parse_thresholds, _run_filter,
    _seasonal_threshold, _threshold_at_time,
)
from .cmems_preprocess import infer_source
from .ocetrac_tracking import TrackingConfig, is_global_longitude, track_anomalies
from .time_axis import attach_time_support, event_time_span, time_axis_report

DIMS = ('time', 'depth', 'latitude', 'longitude')
UNITS = {'thetao': {'degrees_C', 'degC'}, 'o2': {'mmol m-3'}, 'ph': {'1'}}
LABELS = {'thetao': '高温', 'o2': '低氧', 'ph': '高酸度'}


@dataclass(frozen=True)
class CompoundConfig:
    min_duration_steps: int = 1
    min_component_cells: int = 1
    exclusive_pairs: bool = False
    bootstrap_repeats: int = 200
    permutation_repeats: int = 199
    block_length: int = 5
    random_seed: int = 42
    tracking: TrackingConfig = field(default_factory=TrackingConfig)


@dataclass
class CompoundResult:
    masks: xr.Dataset
    events: list[dict]
    single_events: list[dict]
    geojson: dict
    report: dict
    tracks_geojson: dict = field(default_factory=lambda: {'type': 'FeatureCollection', 'features': []})
    lineage: list[dict] = field(default_factory=list)


def load_compound_inputs(paths: Sequence[str | Path]) -> xr.Dataset:
    """Reject spatial mismatches instead of silently taking coordinate unions."""
    if not paths:
        raise ValueError('At least one input file is required')
    sources = []
    try:
        for path in paths:
            ds = xr.open_dataset(path, decode_cf=True, mask_and_scale=True)
            sources.append(ds)
            for coord in DIMS[1:]:
                if coord not in ds.coords:
                    raise ValueError(f'{path}: missing {coord}; preprocess inputs first')
                if not np.array_equal(ds[coord].values, sources[0][coord].values):
                    raise ValueError(f'{path}: {coord} differs; preprocess to one grid/depth first')
            for variable in VARIABLE_RULES:
                if variable in ds:
                    ds[variable].attrs.setdefault('source_products', ds.attrs.get('product_id', infer_source(Path(path), ds)))
                    ds[variable].attrs.setdefault('source_file', str(path))
        merged = xr.merge(sources, join='outer', compat='no_conflicts').load()
        merged.attrs['input_files'] = ';'.join(map(str, paths))
        return merged
    finally:
        for ds in sources:
            ds.close()


def _validate(dataset: xr.Dataset, variables: Sequence[str], single: DetectionConfig, cfg: CompoundConfig) -> xr.Dataset:
    if len(set(variables)) != len(variables) or not 1 <= len(variables) <= 3:
        raise ValueError('Select one, two or three distinct variables')
    if single.frequency not in {'daily', 'monthly', 'mixed'}:
        raise ValueError('Compound detection requires daily, monthly or mixed frequency')
    if single.frequency == 'mixed' and (single.min_duration_steps != 1 or cfg.min_duration_steps != 1 or single.gap_tolerance_steps):
        raise ValueError('Mixed-scale tracking uses per-slice anomalies: duration=1 and gap_tolerance=0')
    if min(single.min_duration_steps, single.min_component_cells, cfg.min_duration_steps, cfg.min_component_cells) < 1:
        raise ValueError('Duration and minimum object size must be positive')
    if single.connectivity not in {1, 2, 3, 4}:
        raise ValueError('Connectivity must be between 1 and 4')
    if not (0 < single.low_percentile < single.high_percentile < 1):
        raise ValueError('Percentiles must satisfy 0 < low < high < 1')
    if cfg.block_length < 1 or min(cfg.bootstrap_repeats, cfg.permutation_repeats) < 0:
        raise ValueError('Block length must be positive; repeat counts cannot be negative')
    if single.gap_tolerance_steps < 0:
        raise ValueError('Gap tolerance cannot be negative')
    if cfg.exclusive_pairs and len(variables) != 3:
        raise ValueError('Exclusive pairs require all three variables, including third-variable validity')
    ds = dataset.copy()
    for coord in DIMS:
        if coord not in ds.coords or ds[coord].dims != (coord,):
            raise ValueError(f'Missing or non-1D coordinate: {coord}')
    if not np.issubdtype(ds.time.dtype, np.datetime64):
        raise ValueError('Time must be decoded datetime64, not numeric indices')
    if np.isnat(ds.time.values).any() or ds.time.size == 0:
        raise ValueError('Time is empty or contains NaT')
    for coord in DIMS:
        values = ds[coord].values
        if np.unique(values).size != values.size:
            raise ValueError(f'Duplicate {coord} coordinates')
        if coord != 'time' and (not np.isfinite(values).all() or values.size == 0):
            raise ValueError(f'Invalid {coord} coordinates')
        ds = ds.sortby(coord)
    if ds.latitude.size < 2 or ds.longitude.size < 2:
        raise ValueError('At least two latitude and longitude centers are needed for cell bounds')
    if float(ds.depth.min()) < 0:
        raise ValueError('Depth must be non-negative, positive downward')
    if float(abs(ds.latitude).max()) > 90 or float(abs(ds.longitude).max()) > 180:
        raise ValueError('Use latitude [-90,90], longitude [-180,180]')
    for variable in variables:
        if variable not in VARIABLE_RULES or variable not in ds:
            raise ValueError(f'Missing or unsupported variable: {variable}')
        if set(ds[variable].dims) != set(DIMS):
            raise ValueError(f'{variable}: expected dimensions {DIMS}')
        if ds[variable].attrs.get('units') not in UNITS[variable]:
            raise ValueError(f'{variable}: preprocess units first; expected {UNITS[variable]}')
        ds[variable] = ds[variable].transpose(*DIMS)
        # Ignore globally absent dates added by merging different input timelines.
        observed = ds.time.values[np.isfinite(ds[variable].values).any(axis=(1, 2, 3))]
        if single.frequency == 'daily' and observed.size > 1:
            delta = np.diff(observed.astype('datetime64[D]').astype('int64'))
            if np.median(delta) > 1:
                raise ValueError(f'{variable}: not daily; monthly/sparse data cannot be expanded into daily data')
        if single.frequency == 'monthly':
            months = observed.astype('datetime64[M]')
            if np.unique(months).size != months.size:
                raise ValueError(f'{variable}: multiple samples per month; aggregate to monthly first')
    time = ds.time.values
    if single.frequency == 'daily':
        if np.unique(time.astype('datetime64[D]')).size != time.size:
            raise ValueError('Multiple timestamps per day; aggregate first')
        if not np.all(time == time.astype('datetime64[D]')):
            raise ValueError('Normalize daily timestamps to UTC midnight before compound matching')
        full = np.arange(time.min().astype('datetime64[D]'), time.max().astype('datetime64[D]') + np.timedelta64(1, 'D'))
    elif single.frequency == 'monthly':
        months = time.astype('datetime64[M]')
        if np.unique(months).size != time.size:
            raise ValueError('Monthly timestamps differ between sources; normalize month coordinates first')
        ds = ds.assign_coords(time=months.astype('datetime64[ns]'))
        full = np.arange(months.min(), months.max() + np.timedelta64(1, 'M')).astype('datetime64[ns]')
    else:
        full = time
    if full.size > 10_000:
        raise ValueError('More than 10000 time steps; subset the period before this in-memory prototype')
    ds = ds.reindex(time=full)
    return attach_time_support(ds, single.frequency)


def _bounds(values: np.ndarray) -> np.ndarray:
    return np.r_[values[0] - (values[1]-values[0])/2, (values[:-1]+values[1:])/2, values[-1]+(values[-1]-values[-2])/2]


def _geometry_metrics(ds: xr.Dataset) -> tuple[np.ndarray, np.ndarray | None, np.ndarray, np.ndarray]:
    yb = np.clip(_bounds(ds.latitude.values.astype(float)), -90, 90)
    xb = _bounds(ds.longitude.values.astype(float))
    area = 6371.0088**2 * np.diff(np.sin(np.deg2rad(yb)))[:, None] * np.deg2rad(np.diff(xb))[None, :]
    thickness = None if ds.depth.size == 1 else np.diff(np.maximum(_bounds(ds.depth.values.astype(float)), 0))
    return area, thickness, yb, xb


def _footprint_geometry(footprint: np.ndarray, yb: np.ndarray, xb: np.ndarray) -> dict:
    # Merge contiguous runs first, rather than constructing millions of cells.
    strips = []
    for y, row in enumerate(footprint):
        edges = np.diff(np.r_[False, row, False].astype(int))
        for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            rectangle = box(xb[start], yb[y], xb[end], yb[y+1])
            for shift in (-360, 0, 360):
                clipped = translate(rectangle, xoff=shift).intersection(box(-180, -90, 180, 90))
                if not clipped.is_empty and clipped.area > 0:
                    strips.append(clipped)
    return mapping(unary_union(strips))


def _centroid(ds: xr.Dataset, zi, yi, xi, weights) -> dict:
    lon = ds.longitude.values[xi]
    if is_global_longitude(ds.longitude.values):
        angle = np.deg2rad(lon)
        longitude = np.rad2deg(np.arctan2(np.average(np.sin(angle), weights=weights),
                                         np.average(np.cos(angle), weights=weights)))
    else:
        longitude = np.average(lon, weights=weights)
    return {'longitude': float(longitude),
            'latitude': float(np.average(ds.latitude.values[yi], weights=weights)),
            'depth_m': float(np.average(ds.depth.values[zi], weights=weights))}


def _objects(mask: xr.DataArray, valid: xr.DataArray, ds: xr.Dataset, variables: Sequence[str], thresholds: dict[str, xr.DataArray], prefix: str, minimum_cells: int, tracking_config: TrackingConfig):
    area, thickness, yb, xb = _geometry_metrics(ds)
    volume = None if thickness is None else thickness[:, None, None] * area[None, :, :] / 1000
    tracking = track_anomalies(mask, ds, tracking_config,
                               cell_volume=None if volume is None else volume*1e9)
    labels = tracking.labels
    events, features, snapshots = [], [], []
    event_ids = {}
    for label in np.unique(labels[labels > 0]):
        active = labels == label
        if int(active.sum()) < minimum_cells:
            labels[active] = 0
            continue
        ti, zi, yi, xi = np.where(active)
        occupied_times = np.unique(ti)
        footprint = active.any(axis=(0, 1))
        per_time_area = (active.any(axis=1) * area).sum(axis=(1, 2))
        per_time_volume = None if volume is None else (active * volume).sum(axis=(1, 2, 3))
        event_id = f'{prefix}-{np.datetime_as_string(ds.time.values[ti.min()], unit="D")}-{len(events)+1:03d}'
        event_ids[int(label)] = event_id
        weights = area[yi, xi] if thickness is None else volume[zi, yi, xi]
        interval_days = ((ds.time_end.values-ds.time_start.values) / np.timedelta64(1, 'D')).astype(float)
        metrics = {}
        for variable in variables:
            vals = ds[variable].values[active].astype(float)
            threshold = thresholds[variable].values[active].astype(float)
            excess = vals-threshold if variable == 'thetao' else threshold-vals
            metrics[variable] = {
                'units': ds[variable].attrs['units'],
                'minimum': float(vals.min()), 'maximum': float(vals.max()),
                'peak_excess': float(excess.max()),
                'mean_excess': float(np.average(excess, weights=weights)),
                'time_weighted_mean_excess': float(np.average(excess, weights=weights*interval_days[ti])),
                'summed_voxel_time_excess': float(excess.sum()),
                'summed_voxel_excess_days': float((excess*interval_days[ti]).sum()),
            }
        span = event_time_span(ds, occupied_times)
        event = {
            'event_id': event_id, 'label_id': int(label), 'variables': list(variables),
            'event_type': '+'.join(LABELS[v] for v in variables) + ('复合事件' if len(variables)>1 else '事件'),
            'start_time': span['start'], 'end_time': span['end'],
            'time_span': span,
            'duration_steps': int(occupied_times.size),
            'duration_unit': {'daily': 'day', 'monthly': 'month', 'mixed': 'native_slice'}[span['precision']],
            'voxel_time_count': int(active.sum()),
            'depth_range_m': [float(ds.depth.values[zi.min()]), float(ds.depth.values[zi.max()])],
            'centroid': _centroid(ds, zi, yi, xi, weights),
            'footprint_area_km2': float((footprint * area).sum()),
            'max_area_km2': float(per_time_area.max()),
            'max_volume_km3': None if volume is None else float(per_time_volume.max()),
            'summed_volume_time_km3_steps': None if volume is None else float(per_time_volume.sum()),
            'integrated_volume_km3_days': None if volume is None else float((per_time_volume*interval_days).sum()),
            'mean_available_fraction_in_bbox': float(valid.values[np.ix_(occupied_times, np.arange(zi.min(), zi.max()+1), np.arange(yi.min(), yi.max()+1), np.arange(xi.min(), xi.max()+1))].mean()),
            'metrics': metrics,
            'data_sources': {v: ds[v].attrs.get('source_products', ds[v].attrs.get('source_product', ds.attrs.get('source_products', 'unspecified'))) for v in variables},
            'tracking': tracking.metadata,
        }
        for v in variables:
            if f'{v}_source_product' in ds.coords:
                event['data_sources'][v] = sorted(set(map(str, ds[f'{v}_source_product'].values[occupied_times])))
        trajectory = []
        for t in occupied_times:
            z, y, x = np.where(active[t])
            weight = area[y, x] if volume is None else volume[z, y, x]
            nodes = [f'{prefix}:O-{t:06d}-{int(o)}' for o in np.unique(tracking.objects[t][active[t]]) if o]
            snapshot = {'event_id': event_id, 'time': np.datetime_as_string(ds.time.values[t], unit='D'),
                        'time_end_exclusive': np.datetime_as_string(ds.time_end.values[t], unit='D'),
                        'time_scale': str(ds.time_scale.values[t]), 'object_nodes': nodes,
                        'centroid': _centroid(ds, z, y, x, weight),
                        'area_km2': float(per_time_area[t]),
                        'volume_km3': None if volume is None else float(per_time_volume[t])}
            trajectory.append(snapshot)
            snapshots.append({'type': 'Feature', 'id': f'{event_id}@{snapshot["time"]}',
                              'properties': snapshot, 'geometry': _footprint_geometry(active[t].any(axis=0), yb, xb)})
        event['trajectory'] = trajectory
        event['footprint_semantics'] = 'union of occupied horizontal cell bounds over all event times/depths'
        events.append(event)
        features.append({'type': 'Feature', 'id': event_id, 'properties': event.copy(), 'geometry': _footprint_geometry(footprint, yb, xb)})
    lineage = []
    for link in tracking.links:
        if link['parent_label'] not in event_ids or link['child_label'] not in event_ids:
            continue
        lineage.append({**link, 'parent_node': prefix+':'+link['parent_node'],
                        'child_node': prefix+':'+link['child_node'],
                        'parent_event_id': event_ids[link['parent_label']],
                        'child_event_id': event_ids[link['child_label']]})
    for event in events:
        event['parent_event_ids'] = sorted({link['parent_event_id'] for link in lineage
                                           if link['child_event_id'] == event['event_id'] and link['parent_event_id'] != event['event_id']})
        event['child_event_ids'] = sorted({link['child_event_id'] for link in lineage
                                          if link['parent_event_id'] == event['event_id'] and link['child_event_id'] != event['event_id']})
        event['lineage_relations'] = sorted({link['relation'] for link in lineage
                                           if event['event_id'] in (link['parent_event_id'], link['child_event_id'])})
    for feature, event in zip(features, events):
        feature['properties'] = event.copy()
    return events, labels, features, snapshots, lineage, tracking.metadata


def _probability_counts(states: np.ndarray, availability: np.ndarray) -> np.ndarray:
    common = availability.all(axis=0)
    axes = tuple(range(1, common.ndim))
    return np.stack([common.sum(axis=axes), *(s.__and__(common).sum(axis=axes) for s in states), (states.all(axis=0)&common).sum(axis=axes)], axis=1)


def _lmf(counts: np.ndarray) -> float | None:
    n = counts[0]
    if n == 0 or np.any(counts[1:-1] == 0):
        return None
    return float((counts[-1]/n) / np.prod(counts[1:-1]/n))


def _statistics(states: np.ndarray, availability: np.ndarray, cfg: CompoundConfig) -> dict:
    counts_by_time = _probability_counts(states, availability)
    counts = counts_by_time.sum(axis=0)
    value = _lmf(counts)
    ntime = states.shape[1]
    supported_times = int((counts_by_time[:, 0] > 0).sum())
    result = {'lmf': value, 'sample_count': int(counts[0]), 'time_count': ntime,
              'valid_time_count': supported_times, 'joint_count': int(counts[-1]),
              'marginal_counts': counts[1:-1].astype(int).tolist(),
              'marginal_probabilities': [float(x/counts[0]) if counts[0] else None for x in counts[1:-1]],
              'joint_probability': float(counts[-1]/counts[0]) if counts[0] else None,
              'ci_95': None, 'permutation_p_value': None, 'bootstrap_valid_repeats': 0,
              'interpretation': 'statistical co-occurrence of duration-qualified single-variable states; not ecological damage',
              'status': 'ok', 'warnings': ['Pooled regional LMF can reflect spatial heterogeneity; it is not a per-grid-cell dependency estimate']}
    if value is None:
        result['status'] = 'undefined'
        result['warnings'].append('No common support or at least one marginal probability is zero')
        return result
    if supported_times < max(3*cfg.block_length, 10):
        result['status'] = 'insufficient_temporal_samples'
        result['warnings'].append('Fewer than 3 supported time blocks or 10 time steps; CI and permutation test omitted')
        return result
    rng = np.random.default_rng(cfg.random_seed)
    estimates = []
    for _ in range(cfg.bootstrap_repeats):
        starts = rng.integers(0, ntime, size=int(np.ceil(ntime/cfg.block_length)))
        indexes = ((starts[:, None]+np.arange(cfg.block_length)) % ntime).ravel()[:ntime]
        estimate = _lmf(counts_by_time[indexes].sum(axis=0))
        if estimate is not None:
            estimates.append(estimate)
    result['bootstrap_valid_repeats'] = len(estimates)
    if len(estimates) >= max(20, int(0.8*cfg.bootstrap_repeats)):
        result['ci_95'] = np.quantile(estimates, [0.025, 0.975]).tolist()
    elif cfg.bootstrap_repeats:
        result['warnings'].append('Too few valid bootstrap replicates; CI omitted')
    null_values = []
    blocks = [np.arange(i, min(i+cfg.block_length, ntime)) for i in range(0, ntime, cfg.block_length)]
    for _ in range(cfg.permutation_repeats):
        permuted_states, permuted_valid = states.copy(), availability.copy()
        for index in range(1, states.shape[0]):
            order = np.concatenate([blocks[i] for i in rng.permutation(len(blocks))])
            permuted_states[index] = states[index, order]
            permuted_valid[index] = availability[index, order]
        estimate = _lmf(_probability_counts(permuted_states, permuted_valid).sum(axis=0))
        if estimate is not None:
            null_values.append(estimate)
    result['permutation_valid_repeats'] = len(null_values)
    if len(null_values) >= max(20, int(0.8*cfg.permutation_repeats)):
        result['permutation_p_value'] = float((1+np.sum(np.asarray(null_values) >= value))/(1+len(null_values)))
    elif cfg.permutation_repeats:
        result['warnings'].append('Too few valid permutations; p value omitted')
    result['warnings'].append('Exploratory temporal-block inference assumes stationarity; spatial cells are not independent samples')
    return result


def _variable_threshold(data: xr.DataArray, variable: str, single: DetectionConfig,
                        baselines: Mapping[str, xr.Dataset] | None) -> xr.DataArray:
    if single.threshold_mode == 'absolute':
        value = single.absolute_thresholds.get(variable)
        if value is None or not np.isfinite(value):
            raise ValueError(f'Missing or invalid absolute threshold for {variable}')
        return xr.full_like(data, float(value))
    if single.threshold_mode != 'seasonal_percentile':
        raise ValueError('Unknown threshold mode')
    threshold = xr.full_like(data, np.nan, dtype=float)
    for scale in sorted(set(data.time_scale.values.astype(str))):
        indexes = np.flatnonzero(data.time_scale.values == scale)
        target = data.isel(time=indexes)
        if baselines and scale in baselines and variable not in baselines[scale]:
            raise ValueError(f'{scale} baseline is missing {variable}')
        reference = target if not baselines or scale not in baselines else baselines[scale][variable]
        for coord in DIMS[1:]:
            if not np.array_equal(reference[coord].values, data[coord].values):
                raise ValueError(f'{scale} baseline {coord} differs; preprocess to the analysis grid')
        if reference.attrs.get('units') not in UNITS[variable]:
            raise ValueError(f'{scale} baseline units differ for {variable}')
        if reference.time.size > 1:
            months = reference.time.values.astype('datetime64[M]')
            if scale == 'monthly' and np.unique(months).size != len(months):
                raise ValueError('Monthly baseline contains multiple samples per month')
            if scale == 'daily' and np.median(np.diff(reference.time.values.astype('datetime64[D]').astype('int64'))) > 1:
                raise ValueError('Daily baseline must not use monthly data')
        field = _seasonal_threshold(reference, variable, replace(single, frequency=scale))
        aligned = _threshold_at_time(field.reindex(month=np.arange(1, 13)), target).transpose(*DIMS)
        threshold.values[indexes] = aligned.values
    return threshold


def detect_compound_events(dataset: xr.Dataset, variables: Sequence[str] = ('thetao', 'o2', 'ph'), single_config: DetectionConfig | None = None, config: CompoundConfig | None = None, baselines: Mapping[str, xr.Dataset] | None = None) -> CompoundResult:
    single = single_config or DetectionConfig()
    cfg = config or CompoundConfig()
    ds = _validate(dataset, variables, single, cfg)
    thresholds, states, available, singles, single_labels, single_reports = {}, {}, {}, [], {}, {}
    output = xr.Dataset(coords=ds.coords)
    single_features, single_snapshots, single_lineage, tracking_metadata = [], [], [], {}
    for variable in variables:
        data = ds[variable]
        threshold = _variable_threshold(data, variable, single, baselines)
        valid = np.isfinite(data) & np.isfinite(threshold)
        raw = ((data > threshold) if variable == 'thetao' else (data < threshold)) & valid
        mask = _run_filter(raw, single, valid=valid)
        events, labels, geometry, snapshots, lineage, metadata = _objects(mask, valid, ds, [variable], {variable: threshold}, variable.upper(), single.min_component_cells, cfg.tracking)
        single_features.extend(geometry)
        single_snapshots.extend(snapshots)
        single_lineage.extend(lineage)
        tracking_metadata[variable] = metadata
        # Remove sub-minimum spatial objects before compound matching.
        span = xr.DataArray(labels > 0, dims=DIMS, coords=mask.coords)
        # A merged normal gap is inside a single-event span, but cannot count as
        # simultaneous abnormality in a compound event or LMF numerator.
        mask = span & raw
        thresholds[variable], states[variable], available[variable], single_labels[variable] = threshold, mask, valid, labels
        singles.extend(events)
        output[f'{variable}_threshold'] = threshold
        output[f'{variable}_event_mask'] = mask.astype('int8')
        output[f'{variable}_event_span_mask'] = span.astype('int8')
        output[f'{variable}_valid_mask'] = valid.astype('int8')
        output[f'{variable}_label'] = xr.DataArray(labels.astype('int32'), dims=DIMS, coords=mask.coords)
        single_reports[variable] = {'event_count': len(events), 'valid_fraction': float(valid.mean()), 'observed_fraction': float(np.isfinite(data).mean()), 'valid_time_count': int(valid.any(dim=DIMS[1:]).sum())}
    all_events, features, reports, all_snapshots, all_lineage = [], [], {}, list(single_snapshots), list(single_lineage)
    for size in range(2, len(variables)+1):
        for group in combinations(variables, size):
            key = '_'.join(group)
            valid = available[group[0]].copy()
            overlap = states[group[0]].copy()
            for variable in group[1:]:
                valid = valid & available[variable]
                overlap = overlap & states[variable]
            if cfg.exclusive_pairs and size == 2:
                other = next(v for v in variables if v not in group)
                valid = valid & available[other]
                overlap = overlap & ~states[other]
            overlap = overlap & valid
            temporal_config = replace(single, min_duration_steps=cfg.min_duration_steps, gap_tolerance_steps=0)
            compound = _run_filter(overlap, temporal_config, valid=valid)
            events, labels, geometry, snapshots, lineage, metadata = _objects(compound, valid, ds, group, thresholds, 'CE-'+key.upper(), cfg.min_component_cells, cfg.tracking)
            tracking_metadata[key] = metadata
            all_snapshots.extend(snapshots)
            all_lineage.extend(lineage)
            for event in events:
                active = labels == event['label_id']
                event['matching_mode'] = 'simultaneous' if cfg.min_duration_steps == 1 else 'persistent_overlap'
                event['pair_semantics'] = 'exclusive' if cfg.exclusive_pairs and size == 2 else 'inclusive'
                event['source_event_ids'] = [e['event_id'] for e in singles if e['variables'][0] in group and np.any(single_labels[e['variables'][0]][active] == e['label_id'])]
            for feature, event in zip(geometry, events):
                feature['properties'] = event.copy()
            output[f'{key}_overlap_mask'] = overlap.astype('int8')
            output[f'{key}_event_mask'] = xr.DataArray((labels > 0).astype('int8'), dims=DIMS, coords=compound.coords)
            output[f'{key}_valid_mask'] = valid.astype('int8')
            output[f'{key}_label'] = xr.DataArray(labels.astype('int32'), dims=DIMS, coords=compound.coords)
            stacked_states = np.stack([states[v].values for v in group])
            stacked_valid = np.stack([available[v].values for v in group])
            if single.frequency == 'mixed':
                stats = {'lmf': None, 'status': 'separated_by_time_scale',
                         'by_time_scale': {scale: _statistics(stacked_states[:, ds.time_scale.values == scale], stacked_valid[:, ds.time_scale.values == scale], cfg)
                                           for scale in sorted(set(ds.time_scale.values.astype(str)))},
                         'warnings': ['Monthly and daily opportunities are not pooled into one LMF.']}
            else:
                stats = _statistics(stacked_states, stacked_valid, cfg)
            stats['variables'] = list(group)
            stats['sample_semantics'] = 'inclusive simultaneous overlap before compound-duration filtering; valid time-depth-grid opportunities'
            reports[key] = {'event_count': len(events), 'overlap_voxel_time_count': int(overlap.sum()), 'qualified_voxel_time_count': int((labels > 0).sum()), 'common_valid_count': int(valid.sum()), 'statistics': stats}
            all_events.extend(events)
            features.extend(geometry)
    if len(variables) == 1:
        all_events, features = list(singles), single_features
    warnings = ['Cell volumes approximate layers from depth-center midpoints; no explicit seafloor/cell bounds supplied.',
                'Tracking currently loads an analysis cube in memory; distributed global-scale execution is not implemented.',
                'Monthly/daily handover is an application adaptation; monthly means do not reveal daily onset or cessation.']
    if single.threshold_mode == 'seasonal_percentile' and not baselines and not single.baseline_start and not single.baseline_end:
        warnings.append('Threshold baseline defaults to input period; supply a scientifically justified long baseline for research use')
    if ds.time.size < max(single.min_duration_steps, cfg.min_duration_steps):
        warnings.append('Time window is shorter than the required duration; no sustained event can be confirmed')
    if any(r['valid_fraction'] == 0 for r in single_reports.values()):
        warnings.append('At least one variable has no valid data/threshold support')
    if any(r['valid_time_count'] < 2 for r in single_reports.values()):
        warnings.append('At least one variable has fewer than two valid timestamps; its native frequency cannot be verified')
    status = 'complete' if all_events else 'no_events'
    if (len(variables) > 1 and not any(r['common_valid_count'] for r in reports.values())) or not any(r['valid_fraction'] for r in single_reports.values()):
        status = 'no_common_data'
    report = {'status': status, 'data_kind': dataset.attrs.get('data_kind', 'unspecified'), 'single_config': asdict(single), 'compound_config': asdict(cfg),
              'dimensions': dict(ds.sizes), 'frequency': single.frequency, 'single_variables': single_reports,
              'combinations': reports, 'event_count': len(all_events), 'single_event_count': len(singles),
              'input_files': dataset.attrs.get('input_files', ''), 'warnings': warnings,
              'time_axis': time_axis_report(ds), 'tracking': tracking_metadata,
              'threshold_baselines': {scale: {'start': str(reference.time.values.min())[:10], 'end': str(reference.time.values.max())[:10], 'input_files': reference.attrs.get('input_files', '')}
                                      for scale, reference in (baselines or {}).items()},
              'lineage_link_count': len(all_lineage)}
    output.attrs.update({'frequency': single.frequency, 'description': 'single states and aligned compound overlap; 0 event with valid_mask=0 is unknown, not normal', 'input_files': report['input_files'], 'data_kind': report['data_kind']})
    return CompoundResult(output, all_events, singles, {'type': 'FeatureCollection', 'features': features}, report,
                          {'type': 'FeatureCollection', 'features': all_snapshots}, all_lineage)


def write_compound_result(result: CompoundResult, output_dir: str | Path) -> None:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    encoding = {name: {'zlib': True, 'complevel': 4} for name in result.masks.data_vars}
    result.masks.to_netcdf(directory/'masks.nc', engine='h5netcdf', encoding=encoding)
    for name, content in [('events.json', result.events), ('single_events.json', result.single_events), ('events.geojson', result.geojson), ('quality.json', result.report), ('tracks.geojson', result.tracks_geojson), ('lineage.json', result.lineage)]:
        (directory/name).write_text(json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', nargs='+', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--variables', nargs='+', choices=tuple(VARIABLE_RULES), default=list(VARIABLE_RULES))
    parser.add_argument('--frequency', choices=('daily', 'monthly'), default='daily')
    parser.add_argument('--threshold-mode', choices=('seasonal_percentile', 'absolute'), default='seasonal_percentile')
    parser.add_argument('--threshold', action='append', default=[])
    parser.add_argument('--high-percentile', type=float, default=0.90)
    parser.add_argument('--low-percentile', type=float, default=0.10)
    parser.add_argument('--baseline-start')
    parser.add_argument('--baseline-end')
    parser.add_argument('--single-min-duration', type=int, default=1)
    parser.add_argument('--min-duration', type=int, default=1, help='Default: per-slice anomalies; >1 explicitly enables legacy fixed-grid duration filtering')
    parser.add_argument('--baseline-input', nargs='+', help='Aligned reference files at the same native time scale')
    parser.add_argument('--tracking-mode', choices=('auto', 'surface', 'volume'), default='auto')
    parser.add_argument('--min-object-cells', type=int, default=1)
    parser.add_argument('--containment-threshold', type=float, default=0.0)
    parser.add_argument('--gap-tolerance', type=int, default=0, help='Single-variable short-gap merging; compound gaps are never filled')
    parser.add_argument('--min-component-cells', type=int, default=1)
    parser.add_argument('--exclusive-pairs', action='store_true')
    parser.add_argument('--block-length', type=int, default=5)
    parser.add_argument('--bootstrap-repeats', type=int, default=200)
    parser.add_argument('--permutation-repeats', type=int, default=199)
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    try:
        single = DetectionConfig(frequency=args.frequency, threshold_mode=args.threshold_mode, absolute_thresholds=_parse_thresholds(args.threshold), high_percentile=args.high_percentile, low_percentile=args.low_percentile, baseline_start=args.baseline_start, baseline_end=args.baseline_end, min_duration_steps=args.single_min_duration, gap_tolerance_steps=args.gap_tolerance)
        cfg = CompoundConfig(min_duration_steps=args.min_duration, min_component_cells=args.min_component_cells, exclusive_pairs=args.exclusive_pairs, block_length=args.block_length, bootstrap_repeats=args.bootstrap_repeats, permutation_repeats=args.permutation_repeats, random_seed=args.seed,
                             tracking=TrackingConfig(mode=args.tracking_mode, min_object_cells=args.min_object_cells, containment_threshold=args.containment_threshold))
        baselines = {args.frequency: load_compound_inputs(args.baseline_input)} if args.baseline_input else None
        result = detect_compound_events(load_compound_inputs(args.input), args.variables, single, cfg, baselines)
        write_compound_result(result, args.output)
        print(json.dumps(result.report, ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(2, f'Compound analysis failed: {error}\n')


if __name__ == '__main__':
    main()
