"""Reproducible, explicitly synthetic global-grid handover demonstration.

Run: python -m data_processing.demo_evolution
No CMEMS credentials, downloads or scientific claims are involved.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import xarray as xr

from .compound_detection import write_compound_result
from .event_detection import DetectionConfig
from .evolution import detect_evolution


def demo_cube(dates, era: str) -> xr.Dataset:
    coords = {'time': np.asarray(dates, dtype='datetime64[ns]'), 'depth': [5., 20., 50.],
              'latitude': np.arange(-60., 61., 20.), 'longitude': np.arange(-180., 180., 30.)}
    dims = tuple(coords)
    shape = tuple(len(values) for values in coords.values())
    variables = {}
    for variable, normal, extreme, unit in [('thetao', 20., 30., 'degrees_C'), ('o2', 240., 180., 'mmol m-3'), ('ph', 8.1, 7.9, '1')]:
        values = np.full(shape, normal)
        # One connected water mass straddles the global longitude boundary.
        values[:, :2, 3, 0] = extreme
        values[:, :2, 3, -1] = extreme
        variables[variable] = xr.DataArray(values, dims=dims, coords=coords,
                                           attrs={'units': unit, 'source_products': f'DEMO_SYNTHETIC_{era.upper()}'})
    return xr.Dataset(variables, attrs={'title': '演示数据：全球粗网格月均—日均交接', 'data_kind': 'demo_synthetic'})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='data/processed/ocetrac_evolution_demo')
    args = parser.parse_args()
    directory = Path(args.output)
    directory.mkdir(parents=True, exist_ok=True)
    history = demo_cube(['2024-06-01', '2024-07-01'], 'historical')
    recent = demo_cube(np.arange(np.datetime64('2024-08-01'), np.datetime64('2024-08-08')), 'recent')
    history.to_netcdf(directory/'historical_monthly.nc', engine='h5netcdf')
    recent.to_netcdf(directory/'recent_daily.nc', engine='h5netcdf')
    config = DetectionConfig(threshold_mode='absolute', absolute_thresholds={'thetao': 25., 'o2': 200., 'ph': 8.})
    result = detect_evolution(history, recent, single_config=config)
    write_compound_result(result, directory)
    print(json.dumps({'data_kind': '演示数据，不是实际海洋事件', 'output': str(directory),
                      'compound_event_count': len(result.events), 'single_event_count': len(result.single_events),
                      'triple_event_time_span': result.events[-1]['time_span'],
                      'tracking': result.report['tracking']['thetao_o2_ph'],
                      'cross_scale_links': sum(link['from_scale'] != link['to_scale'] for link in result.lineage)},
                     ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
