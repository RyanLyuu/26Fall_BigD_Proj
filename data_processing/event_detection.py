"""Single-variable extreme-event detection for the CMEMS analysis pipeline.

This compatibility entry point shares thresholds and the Ocetrac tracking
pipeline with compound detection. The default retains per-slice anomalies;
fixed-grid duration filtering is optional. Native time frequency remains
explicit: monthly pH is never expanded into daily observations.

Example::

    python -m data_processing.event_detection \
      --input classroom/09_CMEMS分析预报BGC/cmems_bgc_nrt_bio_20240101_20240107_118-124E_24-30N_0-50m.nc \
             classroom/09_CMEMS分析预报BGC/cmems_bgc_nrt_car_20240101_20240107_118-124E_24-30N_0-50m.nc \
      --variables o2 ph --frequency daily \
      --output data/processed/daily_bgc_events

The output directory contains ``masks.nc``, ``events.json``, ``events.geojson``
and ``quality.json``.
"""

from __future__ import annotations

import argparse
import json
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import xarray as xr

VARIABLE_RULES = {
    "thetao": {"direction": "high", "label": "高温事件", "unit": "degrees_C"},
    "o2": {"direction": "low", "label": "低氧事件", "unit": "mmol m-3"},
    "ph": {"direction": "low", "label": "高酸度事件", "unit": "1"},
}


@dataclass(frozen=True)
class DetectionConfig:
    """Detection parameters recorded in every result report."""

    frequency: str = "daily"
    threshold_mode: str = "seasonal_percentile"
    high_percentile: float = 0.90
    low_percentile: float = 0.10
    absolute_thresholds: Mapping[str, float] = field(default_factory=dict)
    min_duration_steps: int = 1
    gap_tolerance_steps: int = 0
    min_component_cells: int = 1
    baseline_start: str | None = None
    baseline_end: str | None = None
    calendar_group: str = "month"
    connectivity: int = 1


@dataclass
class DetectionResult:
    masks: xr.Dataset
    thresholds: xr.Dataset
    events: list[dict]
    geojson: dict
    report: dict


def _load_inputs(paths: Sequence[str | Path]) -> xr.Dataset:
    from .compound_detection import load_compound_inputs
    return load_compound_inputs(paths)


def _rule(variable: str) -> dict:
    if variable not in VARIABLE_RULES:
        raise ValueError(f"Unsupported variable {variable!r}; use thetao, o2 or ph")
    return VARIABLE_RULES[variable]


def _valid_time_slice(data: xr.DataArray, config: DetectionConfig) -> xr.DataArray:
    result = data
    if config.baseline_start:
        result = result.sel(time=slice(config.baseline_start, None))
    if config.baseline_end:
        result = result.sel(time=slice(None, config.baseline_end))
    if result.time.size == 0:
        raise ValueError(f"No baseline samples remain for {data.name}")
    return result


def _time_step_days(data: xr.DataArray) -> float | None:
    if data.time.size < 2:
        return None
    values = data.time.values.astype("datetime64[s]").astype("int64")
    return float(np.median(np.diff(values)) / 86400.0)


def _validate_frequency(data: xr.DataArray, config: DetectionConfig) -> tuple[bool, str]:
    step = _time_step_days(data)
    if step is None:
        return True, "single time sample; frequency cannot be inferred"
    if config.frequency == "daily":
        valid = 0.75 <= step <= 1.5
        return valid, f"median time step {step:.2f} days"
    if config.frequency == "monthly":
        valid = 27 <= step <= 32
        return valid, f"median time step {step:.2f} days"
    if config.frequency == "any":
        return True, f"median time step {step:.2f} days"
    raise ValueError("frequency must be daily, monthly or any")


def _seasonal_threshold(data: xr.DataArray, variable: str, config: DetectionConfig) -> xr.DataArray:
    baseline = _valid_time_slice(data, config)
    quantile = config.high_percentile if _rule(variable)["direction"] == "high" else config.low_percentile
    if config.calendar_group != "month":
        raise ValueError("Only calendar_group='month' is currently supported")
    # Land, missing depths and absent reference months intentionally stay NaN;
    # validity masks/quality reports expose them without a warning per chunk.
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message='All-NaN slice encountered', category=RuntimeWarning)
        threshold = baseline.groupby("time.month").quantile(quantile, dim="time", skipna=True)
    if "quantile" in threshold.dims:
        threshold = threshold.squeeze("quantile", drop=True)
    return threshold


def _threshold_at_time(threshold: xr.DataArray, data: xr.DataArray) -> xr.DataArray:
    if "month" not in threshold.dims:
        return threshold.broadcast_like(data)
    monthly = []
    for timestamp in data.time.values:
        month = int(np.datetime_as_string(timestamp, unit="M").split("-")[1])
        monthly.append(threshold.sel(month=month).expand_dims(time=[timestamp]))
    return xr.concat(monthly, dim="time", coords="minimal", compat="override")


def _run_filter(mask: xr.DataArray, config: DetectionConfig, valid: xr.DataArray | None = None) -> xr.DataArray:
    """Keep only temporal runs meeting the minimum duration.

    The operation is applied independently to every depth/latitude/longitude
    column. A short gap can be filled only when ``gap_tolerance_steps`` is set.
    """
    original_dims = mask.dims
    mask = mask.transpose('time', ...)
    values = np.asarray(mask.fillna(False).values, dtype=bool)
    if values.ndim == 0 or values.shape[0] == 0:
        return mask.astype(bool)
    flat = values.reshape(values.shape[0], -1)
    filtered = np.zeros_like(flat)
    minimum = max(1, int(config.min_duration_steps))
    gap = max(0, int(config.gap_tolerance_steps))
    validity = np.ones_like(flat) if valid is None else np.asarray(
        valid.transpose(*mask.dims).values, dtype=bool
    ).reshape(flat.shape)
    if minimum == 1 and gap == 0:
        return xr.DataArray((flat & validity).reshape(values.shape), dims=mask.dims,
                            coords=mask.coords, name=mask.name).transpose(*original_dims)
    if config.frequency == 'monthly':
        times = mask.time.values.astype('datetime64[M]').astype('int64')
    else:
        times = mask.time.values.astype('datetime64[D]').astype('int64')
    breaks = np.r_[0, np.flatnonzero(np.diff(times) != 1) + 1, len(times)]
    if config.frequency == 'any':
        breaks = np.array([0, len(times)])
    for column in range(flat.shape[1]):
        for lo, hi in zip(breaks[:-1], breaks[1:]):
            series = (flat[lo:hi, column] & validity[lo:hi, column]).copy()
            if gap:
                edges = np.diff(np.r_[False, ~series, False].astype(int))
                for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
                    if start > 0 and end < series.size and end - start <= gap and validity[lo+start:lo+end, column].all():
                        series[start:end] = True
            start = None
            for index, is_true in enumerate(np.r_[series, False]):
                if is_true and start is None:
                    start = index
                elif not is_true and start is not None:
                    if index - start >= minimum:
                        filtered[lo+start:lo+index, column] = True
                    start = None
    return xr.DataArray(filtered.reshape(values.shape), dims=mask.dims, coords=mask.coords, name=mask.name).transpose(*original_dims)


def _grid_metrics(data: xr.DataArray) -> tuple[np.ndarray, np.ndarray | None]:
    latitude = data.coords.get("latitude")
    longitude = data.coords.get("longitude")
    if latitude is None or longitude is None:
        raise ValueError("Input data must have latitude and longitude coordinates")
    lat = np.asarray(latitude.values, dtype=float)
    lon = np.asarray(longitude.values, dtype=float)
    dlat = float(np.median(np.abs(np.diff(lat)))) if lat.size > 1 else 0.25
    dlon = float(np.median(np.abs(np.diff(lon)))) if lon.size > 1 else 0.25
    earth_radius_km = 6371.0088
    lat_low = np.deg2rad(lat - dlat / 2)
    lat_high = np.deg2rad(lat + dlat / 2)
    area_by_lat = earth_radius_km**2 * np.deg2rad(dlon) * (np.sin(lat_high) - np.sin(lat_low))
    depth = data.coords.get("depth")
    thickness = None
    if depth is not None:
        z = np.asarray(depth.values, dtype=float)
        if z.size == 1:
            thickness = np.ones(1)
        else:
            thickness = np.gradient(z)
            thickness = np.abs(thickness)
    return area_by_lat, thickness


def _event_features(label_field: np.ndarray, event_label: int, data: xr.DataArray, excess: xr.DataArray) -> dict:
    indexes = np.where(label_field == event_label)
    if len(indexes) == 0 or indexes[0].size == 0:
        return {}
    time_index = data.dims.index("time")
    time_values = data.time.values[indexes[time_index]]
    lat_index = data.dims.index("latitude")
    lon_index = data.dims.index("longitude")
    lat_values = data.latitude.values[indexes[lat_index]]
    lon_values = data.longitude.values[indexes[lon_index]]
    area_by_lat, depth_thickness = _grid_metrics(data)
    weights = area_by_lat[indexes[lat_index]]
    if depth_thickness is not None and "depth" in data.dims:
        depth_index = data.dims.index("depth")
        weights = weights * depth_thickness[indexes[depth_index]]
    values = np.asarray(excess.values)[indexes]
    finite = np.isfinite(values)
    values = values[finite]
    weights = weights[finite]
    distinct_time = np.unique(time_values)
    start = np.datetime_as_string(distinct_time.min(), unit="D")
    end = np.datetime_as_string(distinct_time.max(), unit="D")
    area_pairs = {(float(lat), float(lon)) for lat, lon in zip(lat_values, lon_values)}
    horizontal_area = 0.0
    for lat, _ in area_pairs:
        nearest = int(np.argmin(np.abs(data.latitude.values - lat)))
        horizontal_area += float(area_by_lat[nearest])
    depth_min = float(data.depth.values[indexes[data.dims.index("depth")]].min()) if "depth" in data.dims else None
    depth_max = float(data.depth.values[indexes[data.dims.index("depth")]].max()) if "depth" in data.dims else None
    return {
        "start_time": start,
        "end_time": end,
        "duration_steps": int(distinct_time.size),
        "duration_days": int((distinct_time.max() - distinct_time.min()) / np.timedelta64(1, "D") + 1),
        "grid_cells": int(indexes[0].size),
        "area_km2": round(horizontal_area, 3),
        "volume_km3": round(float(weights.sum()) / 1000.0, 3) if depth_thickness is not None else None,
        "depth_range_m": [depth_min, depth_max] if depth_min is not None else None,
        "centroid": {"latitude": round(float(np.nanmean(lat_values)), 5), "longitude": round(float(np.nanmean(lon_values)), 5)},
        "peak_excess": round(float(values.max()), 6) if values.size else None,
        "cumulative_excess": round(float(values.sum()), 6) if values.size else None,
    }


def _geojson_feature(event: dict, event_id: str, variable: str, data: xr.DataArray) -> dict:
    lat = event["centroid"]["latitude"]
    lon = event["centroid"]["longitude"]
    lat_values = np.asarray(data.latitude.values, dtype=float)
    lon_values = np.asarray(data.longitude.values, dtype=float)
    dlat = float(np.median(np.abs(np.diff(lat_values)))) if lat_values.size > 1 else 0.25
    dlon = float(np.median(np.abs(np.diff(lon_values)))) if lon_values.size > 1 else 0.25
    return {
        "type": "Feature",
        "id": event_id,
        "properties": {"event_id": event_id, "variable": variable, **event},
        "geometry": {"type": "Polygon", "coordinates": [[[lon - dlon / 2, lat - dlat / 2], [lon + dlon / 2, lat - dlat / 2], [lon + dlon / 2, lat + dlat / 2], [lon - dlon / 2, lat + dlat / 2], [lon - dlon / 2, lat - dlat / 2]]]},
    }


def detect_variable(data: xr.DataArray, config: DetectionConfig) -> tuple[xr.DataArray, xr.DataArray, list[dict], dict]:
    """Compatibility entry point using the same Ocetrac pipeline as compounds."""
    from .compound_detection import detect_compound_events
    variable = data.name or 'variable'
    _rule(variable)
    result = detect_compound_events(data.to_dataset(), [variable], config)
    report = {**result.report['single_variables'][variable], 'variable': variable,
              'frequency': config.frequency, 'threshold_mode': config.threshold_mode,
              'min_duration_steps': config.min_duration_steps,
              'tracking': result.report['tracking'][variable]}
    return result.masks[f'{variable}_event_mask'], result.masks[f'{variable}_threshold'], result.events, report


def detect_events(dataset: xr.Dataset, variables: Sequence[str], config: DetectionConfig | None = None) -> DetectionResult:
    """Run single-variable detection for the requested variables."""
    config = config or DetectionConfig()
    masks, thresholds, events, reports = {}, {}, [], {}
    features = []
    from .compound_detection import detect_compound_events
    for variable in variables:
        if variable not in dataset:
            reports[variable] = {"status": "missing", "message": "variable not found in input"}
            continue
        try:
            result = detect_compound_events(dataset, [variable], config)
            mask = result.masks[f'{variable}_event_mask']
            threshold = result.masks[f'{variable}_threshold']
            variable_events = result.events
            report = result.report
        except ValueError as error:
            reports[variable] = {"status": "skipped", "message": str(error)}
            continue
        masks[mask.name] = mask
        thresholds[threshold.name] = threshold
        events.extend(variable_events)
        features.extend(result.geojson['features'])
        reports[variable] = {"status": "ok", **report}
    mask_dataset = xr.Dataset(masks)
    threshold_dataset = xr.Dataset(thresholds)
    report = {
        "config": asdict(config),
        "variables": reports,
        "event_count": len(events),
        "event_types": sorted({event["event_type"] for event in events}),
        "source_products": dataset.attrs.get("source_products", ""),
    }
    return DetectionResult(mask_dataset, threshold_dataset, events, {"type": "FeatureCollection", "features": features}, report)


def _parse_thresholds(values: Sequence[str]) -> dict[str, float]:
    result = {}
    for value in values:
        name, separator, threshold = value.partition("=")
        if not separator:
            raise ValueError(f"Threshold must use variable=value, got {value!r}")
        result[name] = float(threshold)
    return result


def _write_result(result: DetectionResult, output_dir: str | Path) -> None:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    xr.merge([result.masks, result.thresholds]).to_netcdf(directory / "masks.nc", engine="h5netcdf")
    (directory / "events.json").write_text(json.dumps(result.events, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "events.geojson").write_text(json.dumps(result.geojson, ensure_ascii=False, indent=2), encoding="utf-8")
    (directory / "quality.json").write_text(json.dumps(result.report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", nargs="+", required=True)
    parser.add_argument("--variables", nargs="+", choices=tuple(VARIABLE_RULES), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--frequency", choices=("daily", "monthly"), default="daily")
    parser.add_argument("--threshold-mode", choices=("seasonal_percentile", "absolute"), default="seasonal_percentile")
    parser.add_argument("--threshold", action="append", default=[], help="Absolute threshold, for example o2=200")
    parser.add_argument("--high-percentile", type=float, default=0.90)
    parser.add_argument("--low-percentile", type=float, default=0.10)
    parser.add_argument("--min-duration", type=int, default=1)
    parser.add_argument("--gap-tolerance", type=int, default=0)
    parser.add_argument("--min-component-cells", type=int, default=1)
    parser.add_argument("--baseline-start")
    parser.add_argument("--baseline-end")
    args = parser.parse_args()
    dataset = _load_inputs(args.input)
    config = DetectionConfig(
        frequency=args.frequency,
        threshold_mode=args.threshold_mode,
        absolute_thresholds=_parse_thresholds(args.threshold),
        high_percentile=args.high_percentile,
        low_percentile=args.low_percentile,
        min_duration_steps=args.min_duration,
        gap_tolerance_steps=args.gap_tolerance,
        min_component_cells=args.min_component_cells,
        baseline_start=args.baseline_start,
        baseline_end=args.baseline_end,
    )
    result = detect_events(dataset, args.variables, config)
    _write_result(result, args.output)
    print(json.dumps(result.report, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
