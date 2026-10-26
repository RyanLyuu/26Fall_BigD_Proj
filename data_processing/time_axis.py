"""Calendar support for native monthly/daily slices, including their handover.

Time labels are not durations: a monthly label denotes a calendar month, not
one day. Intervals are half open, in UTC, and are never expanded into daily
observations. Gaps split tracking blocks even when array indices are adjacent.
"""
from __future__ import annotations

import numpy as np
import xarray as xr


def attach_time_support(dataset: xr.Dataset, frequency: str) -> xr.Dataset:
    if frequency not in {'monthly', 'daily', 'mixed'}:
        raise ValueError('Time scale must be monthly, daily or mixed')
    ds = dataset.copy(deep=False)
    time = ds.time.values.astype('datetime64[ns]')
    if frequency == 'mixed':
        required = {'time_scale', 'time_start', 'time_end'}
        if not required.issubset(ds.coords):
            raise ValueError('Mixed inputs require time_scale/time_start/time_end coordinates; use join_historical_recent')
        scales = ds.time_scale.values.astype(str)
        starts = ds.time_start.values.astype('datetime64[ns]')
        ends = ds.time_end.values.astype('datetime64[ns]')
    else:
        scales = np.full(time.size, frequency)
        starts = time
        ends = np.array([
            ((t.astype('datetime64[M]') + np.timedelta64(1, 'M'))
             if frequency == 'monthly' else t.astype('datetime64[D]') + np.timedelta64(1, 'D'))
            for t in time
        ], dtype='datetime64[ns]')
    if not set(scales).issubset({'monthly', 'daily'}):
        raise ValueError('Each time_scale must be monthly or daily')
    if any(ds[name].dims != ('time',) for name in ('time_scale', 'time_start', 'time_end') if name in ds.coords):
        raise ValueError('Time support coordinates must be one-dimensional over time')
    if np.isnat(starts).any() or np.isnat(ends).any() or not np.array_equal(starts, time):
        raise ValueError('Time labels must equal finite interval starts')
    for i, scale in enumerate(scales):
        start = starts[i]
        expected_start = start.astype('datetime64[M]' if scale == 'monthly' else 'datetime64[D]')
        expected_end = (expected_start + np.timedelta64(1, 'M' if scale == 'monthly' else 'D')).astype('datetime64[ns]')
        if start != expected_start or ends[i] != expected_end:
            raise ValueError(f'{scale} slice must cover exactly one calendar month/day')
    if (ends[:-1] > starts[1:]).any():
        raise ValueError('Time intervals overlap; choose one historical/recent cutover')
    ds = ds.assign_coords(time_scale=('time', scales), time_start=('time', starts), time_end=('time', ends))
    ds.time_end.attrs['description'] = 'exclusive UTC end of the native averaging interval'
    ds.attrs['frequency'] = frequency
    return ds


def tracking_blocks(dataset: xr.Dataset) -> list[slice]:
    starts, ends = dataset.time_start.values, dataset.time_end.values
    breaks = np.r_[0, np.flatnonzero(starts[1:] != ends[:-1]) + 1, len(starts)]
    return [slice(int(a), int(b)) for a, b in zip(breaks[:-1], breaks[1:]) if b > a]


def time_axis_report(dataset: xr.Dataset) -> dict:
    scales = dataset.time_scale.values.astype(str)
    starts, ends = dataset.time_start.values, dataset.time_end.values
    date = lambda t: np.datetime_as_string(t, unit='D')
    gaps = [{'start': date(ends[i]), 'end_exclusive': date(starts[i+1])}
            for i in range(len(starts)-1) if ends[i] != starts[i+1]]
    transitions = [{'from': str(scales[i]), 'to': str(scales[i+1]),
                    'at': date(starts[i+1]), 'contiguous': bool(ends[i] == starts[i+1])}
                   for i in range(len(starts)-1) if scales[i] != scales[i+1]]
    return {'interval_convention': '[start, end), UTC',
            'slice_counts': {scale: int((scales == scale).sum()) for scale in sorted(set(scales))},
            'gaps': gaps, 'transitions': transitions,
            'monthly_semantics': 'Monthly means retain month precision; daily onset/termination is unknown.'}


def event_time_span(dataset: xr.Dataset, indexes: np.ndarray) -> dict:
    indexes = np.unique(indexes)
    starts, ends = dataset.time_start.values[indexes], dataset.time_end.values[indexes]
    scales = dataset.time_scale.values[indexes].astype(str)
    counts = {scale: int((scales == scale).sum()) for scale in sorted(set(scales))}
    return {'start': np.datetime_as_string(starts.min(), unit='D'),
            'end': np.datetime_as_string(ends.max() - np.timedelta64(1, 'D'), unit='D'),
            'end_exclusive': np.datetime_as_string(ends.max(), unit='D'),
            'precision': next(iter(counts)) if len(counts) == 1 else 'mixed',
            'slice_counts': counts,
            'span_days': int((ends.max() - starts.min()) / np.timedelta64(1, 'D')),
            'supported_interval_days': int(((ends-starts) / np.timedelta64(1, 'D')).sum())}
