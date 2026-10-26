"""Build explicitly synthetic quarter-degree display data, never analysis output.

Usage: python scripts/build-map-demo.py /path/to/ne_10m_land.geojson
Requires shapely; this is an offline asset tool, not a frontend dependency.
"""
import json
import math
import sys
from collections import Counter
from pathlib import Path

from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parents[1]
land = json.loads(Path(sys.argv[1]).read_text())
polygons = [shape(f['geometry']).simplify(0.004, preserve_topology=True) for f in land['features']]
coast = {'type': 'FeatureCollection', 'features': [
    {'type': 'Feature', 'properties': {}, 'geometry': mapping(p)} for p in polygons
]}
(ROOT / 'public/maps/land-detail.geojson').write_text(json.dumps(coast, separators=(',', ':')))
land_mask = prep(unary_union(polygons))


def noise(x, y, seed):
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    fx, fy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)

    def corner(a, b):
        value = math.sin(a * 127.1 + b * 311.7 + seed * 74.7) * 43758.5453
        return (value - math.floor(value)) * 2 - 1

    return ((corner(ix, iy) * (1 - fx) + corner(ix + 1, iy) * fx) * (1 - fy)
            + (corner(ix, iy + 1) * (1 - fx) + corner(ix + 1, iy + 1) * fx) * fy)


def field(x, y, phase):
    # Domain-warped multi-scale patches rather than periodic bands or pixel noise.
    u = x + 2.1 * noise(x * 0.27, y * 0.27, 13)
    v = y + 2.1 * noise(x * 0.27, y * 0.27, 29)
    return sum(weight * noise(u * scale, v * scale, phase * 100)
               for scale, weight in [(0.3, 0.5), (0.8, 0.27), (1.7, 0.16), (3.4, 0.07)])


catalog = {'thetao_o2_ph': 'CE-001', 'thetao_o2': 'CE-002', 'thetao': 'CE-003',
           'o2': 'CE-004', 'ph': 'CE-005', 'thetao_ph': 'CE-006', 'o2_ph': 'CE-007'}
variables = ['thetao', 'o2', 'ph']
cells = []
for xi in range(472, 588):
    for yi in range(68, 172):
        lng, lat = xi / 4 + 0.125, yi / 4 + 0.125
        if land_mask.contains(Point(lng, lat)):
            continue
        x, y = lng - 132, lat - 29
        # Taper the display footprint without drawing an artificial rectangular edge.
        envelope = (x / 13.4) ** 2 + (y / 12.6) ** 2
        margin = max(0, envelope - 0.48) * 0.85
        shared = field(x, y, 0.4)
        if 0.5 * shared + 0.5 * field(x, y, 8.3) < -0.06:
            continue
        base = [0.42 * shared + 0.58 * field(x, y, p) + 0.08 - margin for p in (0.9, 3.1, 5.2)]
        thickness = Counter()
        for depth_index, layer_thickness in enumerate([5, 10, 10, 10, 15]):
            mask = [variable for i, variable in enumerate(variables)
                    if base[i] + 0.32 * math.sin(depth_index * 1.3 + i * 1.8)
                    + 0.12 * field(x + depth_index, y - depth_index, i + 9) > 0.1]
            if mask:
                thickness['_'.join(mask)] += layer_thickness
        if not thickness:
            continue
        # One exclusive class per voxel; select the thickest class in the water
        # column, breaking ties by number of affected variables, then stable key.
        combination, affected = max(thickness.items(), key=lambda item: (item[1], item[0].count('_'), item[0]))
        cells.append({'longitude': lng, 'latitude': lat, 'combination': combination,
                      'eventIds': [catalog[combination]], 'affectedFraction': affected / 50, 'valid': True})

frame = {'source': 'demo', 'time': None, 'resolutionDegrees': 0.25, 'depthRange': [0, 50], 'cells': cells}
(ROOT / 'src/mock/grid-demo.json').write_text(json.dumps(frame, separators=(',', ':')) + '\n')
print('Synthetic cells:', len(cells), Counter(c['combination'] for c in cells))
print('Depth fractions:', sorted(set(c['affectedFraction'] for c in cells)))
