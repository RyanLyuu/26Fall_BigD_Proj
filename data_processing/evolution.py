"""Native monthly history → native daily recent evolution with one tracker.

Inputs are already harmonised CMEMS cubes. The handover defaults to the end
of the last historical calendar month. Recent overlapping months are excluded;
missing daily slices remain unknown. No monthly values are broadcast to days.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
import json
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np
import xarray as xr

from .compound_detection import (
    CompoundConfig, CompoundResult, DIMS, _validate, detect_compound_events,
    load_compound_inputs, write_compound_result,
)
from .event_detection import DetectionConfig, VARIABLE_RULES, _parse_thresholds
from .ocetrac_tracking import TrackingConfig
from .time_axis import attach_time_support, time_axis_report


def join_historical_recent(historical: xr.Dataset, recent: xr.Dataset,
                           variables: Sequence[str] = ('thetao', 'o2', 'ph'),
                           cutover: str | None = None) -> xr.Dataset:
    """Return non-overlapping, explicitly bounded native time slices.

    A later recent start is allowed but reported as an interval gap; tracking
    never connects across it. Full daily reindexing also preserves missing days
    inside the recent segment. Grid/depth mismatches are errors, not implicit
    interpolation. Use cmems_preprocess before this function.
    """
    cfg = CompoundConfig()
    history = _validate(historical, variables, DetectionConfig(frequency='monthly'), cfg)
    current = _validate(recent, variables, DetectionConfig(frequency='daily'), cfg)
    for coord in DIMS[1:]:
        if not np.array_equal(history[coord].values, current[coord].values):
            raise ValueError(f'Historical/recent {coord} differs; preprocess to one global grid and depth first')
    boundary = np.datetime64(cutover, 'ns') if cutover else history.time_end.values[-1]
    if np.isnat(boundary) or boundary != boundary.astype('datetime64[M]'):
        raise ValueError('Cutover must be the first day of a calendar month at UTC midnight')
    history = history.isel(time=history.time_end.values <= boundary)
    current = current.isel(time=current.time_start.values >= boundary)
    if not history.time.size or not current.time.size:
        raise ValueError('Cutover leaves an empty historical or recent segment')
    for ds, era in [(history, 'historical'), (current, 'recent')]:
        ds.coords['source_era'] = ('time', np.full(ds.time.size, era))
        for variable in variables:
            source = ds[variable].attrs.get('source_products', ds[variable].attrs.get('source_product', ds.attrs.get('source_products', 'unspecified')))
            ds.coords[f'{variable}_source_product'] = ('time', np.full(ds.time.size, str(source)))
    joined = xr.concat([history[list(variables)], current[list(variables)]], dim='time',
                       data_vars='all', coords='minimal', compat='equals', join='exact', combine_attrs='drop_conflicts')
    joined.attrs.update({'frequency': 'mixed', 'cutover': str(np.datetime_as_string(boundary, unit='D')),
                         'input_files': ';'.join(filter(None, [historical.attrs.get('input_files', ''), recent.attrs.get('input_files', '')])),
                         'time_resolution_policy': 'native monthly then daily; no resampling or artificial daily observations'})
    return attach_time_support(joined, 'mixed')


def detect_evolution(historical: xr.Dataset, recent: xr.Dataset,
                     variables: Sequence[str] = ('thetao', 'o2', 'ph'),
                     single_config: DetectionConfig | None = None,
                     config: CompoundConfig | None = None,
                     cutover: str | None = None,
                     baselines: Mapping[str, xr.Dataset] | None = None) -> CompoundResult:
    single = replace(single_config or DetectionConfig(), frequency='mixed')
    cfg = config or CompoundConfig()
    joined = join_historical_recent(historical, recent, variables, cutover)
    result = detect_compound_events(joined, variables, single, cfg, baselines)
    result.report['cutover'] = joined.attrs['cutover']
    result.report['time_axis'] = time_axis_report(joined)
    if result.report['time_axis']['gaps']:
        result.report['warnings'].append('Historical/recent data contain interval gaps; these start new tracking blocks.')
    result.masks.attrs['cutover'] = joined.attrs['cutover']
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--historical-input', nargs='+', required=True, help='Aligned native monthly NetCDF files')
    parser.add_argument('--recent-input', nargs='+', required=True, help='Aligned native daily NetCDF files, including the handover interval')
    parser.add_argument('--historical-baseline', nargs='+', help='Optional separate monthly reference cube')
    parser.add_argument('--recent-baseline', nargs='+', help='Optional separate daily reference cube')
    parser.add_argument('--cutover', help='First UTC day of the daily segment; defaults to end of last historical month')
    parser.add_argument('--output', required=True)
    parser.add_argument('--variables', nargs='+', choices=tuple(VARIABLE_RULES), default=list(VARIABLE_RULES))
    parser.add_argument('--threshold-mode', choices=('seasonal_percentile', 'absolute'), default='seasonal_percentile')
    parser.add_argument('--threshold', action='append', default=[])
    parser.add_argument('--baseline-start')
    parser.add_argument('--baseline-end')
    parser.add_argument('--high-percentile', type=float, default=.90)
    parser.add_argument('--low-percentile', type=float, default=.10)
    parser.add_argument('--tracking-mode', choices=('auto', 'surface', 'volume'), default='auto')
    parser.add_argument('--min-object-cells', type=int, default=1)
    parser.add_argument('--containment-threshold', type=float, default=0.0)
    parser.add_argument('--exclusive-pairs', action='store_true')
    parser.add_argument('--bootstrap-repeats', type=int, default=200)
    parser.add_argument('--permutation-repeats', type=int, default=199)
    args = parser.parse_args()
    try:
        baselines = {}
        for scale, files in [('monthly', args.historical_baseline), ('daily', args.recent_baseline)]:
            if files:
                baselines[scale] = load_compound_inputs(files)
        single = DetectionConfig(threshold_mode=args.threshold_mode,
                                  absolute_thresholds=_parse_thresholds(args.threshold),
                                  baseline_start=args.baseline_start, baseline_end=args.baseline_end,
                                  high_percentile=args.high_percentile, low_percentile=args.low_percentile)
        cfg = CompoundConfig(exclusive_pairs=args.exclusive_pairs,
                             bootstrap_repeats=args.bootstrap_repeats, permutation_repeats=args.permutation_repeats,
                             tracking=TrackingConfig(mode=args.tracking_mode, min_object_cells=args.min_object_cells,
                                                     containment_threshold=args.containment_threshold))
        result = detect_evolution(load_compound_inputs(args.historical_input), load_compound_inputs(args.recent_input),
                                   args.variables, single, cfg, args.cutover, baselines or None)
        write_compound_result(result, Path(args.output))
        print(json.dumps(result.report, ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(2, f'Evolution analysis failed: {error}\n')


if __name__ == '__main__':
    main()
