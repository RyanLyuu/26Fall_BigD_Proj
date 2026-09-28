"""Normalize and merge CMEMS NetCDF files for compound extreme-event analysis.

The module keeps the downloaded source files untouched and writes a new dataset with
canonical variables (``thetao``, ``o2`` and ``ph``) on one horizontal grid and one
set of physical depth levels.  It is intentionally conservative: missing values
are kept as NaN, no extrapolation is done below the sea floor, and duplicate source
variables are combined by time with the first source winning on duplicate timestamps.

Example:
    python -m data_processing.cmems_preprocess \
      --input data/samples/glorys12_thetao_20240813.nc \
               data/samples/bgc_my_o2_20240813.nc \
               data/samples/bgc_my_o2_ph_202408.nc \
               data/samples/phy_anfc_thetao_20260928.nc \
               data/samples/bgc_anfc_o2_20260928.nc \
               data/samples/bgc_anfc_ph_20260928.nc \
      --bbox 122 123 27 28 --output data/processed/cmems_common.nc
"""

from __future__ import annotations

import argparse
import json
import re
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np
import xarray as xr

CANONICAL_VARIABLES = ("thetao", "o2", "ph")
DEFAULT_DEPTHS = (1.0, 5.0, 10.0, 20.0, 30.0, 40.0, 50.0)
COORD_ALIASES = {
    "latitude": ("latitude", "lat", "nav_lat", "y"),
    "longitude": ("longitude", "lon", "nav_lon", "x"),
    "depth": ("depth", "deptht", "lev", "level", "z"),
    "time": ("time", "time_counter", "datetime", "date"),
}


@dataclass(frozen=True)
class PreprocessConfig:
    """Options shared by the Python API and command-line entry point."""

    target_depths: tuple[float, ...] = DEFAULT_DEPTHS
    bbox: tuple[float, float, float, float] | None = None
    resolution: float = 0.25
    regrid_method: str = "linear"
    min_valid_fraction: float = 0.0
    duplicate_policy: str = "first"


@dataclass
class _Source:
    path: Path
    name: str
    dataset: xr.Dataset


def _normalise_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def infer_source(path: Path, dataset: xr.Dataset) -> str:
    """Infer a short provenance label from the filename and CMEMS metadata."""
    text = " ".join(
        [path.name, str(dataset.attrs.get("title", "")), str(dataset.attrs.get("source", ""))]
    ).lower()
    if "phy_anfc" in text or "analysis and forecast" in text and "temperature" in text:
        return "PHY_ANFC"
    if "bgc_anfc" in text or "forecast" in text and "bgc" in text:
        return "BGC_ANFC"
    if "glorys" in text or "gl12" in text or "multi-year" in text and "temperature" in text:
        return "GLORYS12"
    if "bgc_my" in text or "freebiorys" in text or "reanalysis-bio" in text:
        return "BGC_MY"
    return _normalise_name(path.stem).upper()


def _find_coord(dataset: xr.Dataset, canonical: str) -> str | None:
    aliases = COORD_ALIASES[canonical]
    for candidate in aliases:
        if candidate in dataset.coords:
            return candidate
    for candidate in aliases:
        if candidate in dataset.variables and dataset[candidate].ndim == 1:
            return candidate
    return None


def _rename_coordinates(dataset: xr.Dataset) -> xr.Dataset:
    renames = {}
    for canonical in COORD_ALIASES:
        actual = _find_coord(dataset, canonical)
        if actual and actual != canonical:
            renames[actual] = canonical
    if renames:
        dataset = dataset.rename(renames)
    required = [name for name in ("latitude", "longitude", "time") if name not in dataset.coords]
    if required:
        raise ValueError(f"Missing required coordinate(s): {', '.join(required)}")
    return dataset


def _normalise_longitude(dataset: xr.Dataset) -> xr.Dataset:
    lon = dataset.longitude
    # Use the conventional -180..180 range so sources with different conventions align.
    if float(lon.max()) > 180.0:
        values = ((lon + 180.0) % 360.0) - 180.0
        dataset = dataset.assign_coords(longitude=values)
    return dataset.sortby("longitude").sortby("latitude").sortby("time")


def _mask_invalid_values(dataset: xr.Dataset) -> xr.Dataset:
    result = dataset.copy()
    for name in list(result.data_vars):
        da = result[name]
        if name not in CANONICAL_VARIABLES:
            continue
        # decode_cf usually masks these, but doing it explicitly also covers files
        # written by older CMEMS services.
        for attr in ("_FillValue", "missing_value"):
            value = da.attrs.get(attr)
            if value is not None:
                result[name] = result[name].where(result[name] != value)
        valid_min = da.attrs.get("valid_min")
        valid_max = da.attrs.get("valid_max")
        if valid_min is not None:
            result[name] = result[name].where(result[name] >= float(valid_min))
        if valid_max is not None:
            result[name] = result[name].where(result[name] <= float(valid_max))
    return result


def _normalise_units(dataset: xr.Dataset) -> xr.Dataset:
    """Normalize safe unit aliases and reject unconverted oxygen units."""
    result = dataset.copy()
    for name in result.data_vars:
        units = str(result[name].attrs.get("units", "")).strip().lower()
        compact = re.sub(r"[\s_]+", "", units).replace("**", "^")
        if name == "thetao":
            if compact in {"degrees_c", "degreesc", "degree_c", "degreec", "degc", "celsius", "degree_celsius", "degreecelsius", "degreescelsius"}:
                result[name].attrs["units"] = "degrees_C"
            else:
                raise ValueError(f"Unsupported temperature unit for thetao: {units!r}")
        elif name == "o2":
            aliases = {"mmolm-3", "mmol/m3", "mmolm^-3", "mmol/m^3", "mmol.m-3"}
            equivalent = {"umoll-1", "umol/l", "μmoll-1", "μmol/l"}
            if compact in aliases:
                result[name].attrs["units"] = "mmol m-3"
            elif compact in equivalent:
                # 1 μmol/L is numerically equal to 1 mmol/m³.
                result[name].attrs["units"] = "mmol m-3"
            else:
                raise ValueError(
                    f"Unsupported oxygen unit {units!r}. Convert it to mmol m-3 "
                    "using seawater density before merging sources."
                )
        elif name == "ph":
            if units in {"1", "", "dimensionless", "unitless"}:
                result[name].attrs["units"] = "1"
            else:
                raise ValueError(f"Unsupported pH unit: {units!r}")
    return result


def _extract_variables(dataset: xr.Dataset) -> xr.Dataset:
    found = [name for name in CANONICAL_VARIABLES if name in dataset.data_vars]
    if not found:
        raise ValueError(
            "No canonical variable found. Expected at least one of: "
            + ", ".join(CANONICAL_VARIABLES)
        )
    return dataset[found]


def open_source(path: str | Path, source_name: str | None = None) -> _Source:
    """Open one NetCDF and normalize its coordinates and canonical variables."""
    source_path = Path(path)
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    dataset = xr.open_dataset(source_path, decode_cf=True, mask_and_scale=True)
    dataset = _rename_coordinates(dataset)
    dataset = _normalise_longitude(dataset)
    if "depth" in dataset.coords:
        dataset = dataset.sortby("depth")
    dataset = _normalise_units(_mask_invalid_values(_extract_variables(dataset)))
    name = source_name or infer_source(source_path, dataset)
    dataset.attrs.update({"source_product": name, "source_file": str(source_path)})
    return _Source(source_path, name, dataset)


def _target_coordinates(config: PreprocessConfig, sources: Sequence[_Source]) -> dict[str, np.ndarray]:
    if config.bbox is None:
        raise ValueError("bbox is required when constructing a common target grid")
    lon_min, lon_max, lat_min, lat_max = config.bbox
    if config.resolution <= 0:
        raise ValueError("resolution must be positive")
    # Prefer the BGC grid itself so regridding the finer physical fields does not
    # introduce a second, slightly shifted 0.25-degree grid.
    reference = next((
        source.dataset for source in sources
        if "o2" in source.dataset.data_vars or "ph" in source.dataset.data_vars
    ), None)
    if reference is not None:
        longitude = reference.longitude.values
        latitude = reference.latitude.values
        longitude = longitude[(longitude >= lon_min) & (longitude <= lon_max)]
        latitude = latitude[(latitude >= lat_min) & (latitude <= lat_max)]
    else:
        # Anchor a fallback grid at the nearest resolution multiple. A BGC
        # reference grid is preferred because products can use shifted centers.
        lon_start = np.ceil(lon_min / config.resolution) * config.resolution
        lat_start = np.ceil(lat_min / config.resolution) * config.resolution
        longitude = np.arange(lon_start, lon_max + config.resolution * 0.5, config.resolution)
        latitude = np.arange(lat_start, lat_max + config.resolution * 0.5, config.resolution)
    if longitude.size == 0 or latitude.size == 0:
        raise ValueError("The requested bbox contains no cells in the reference grid")
    longitude = np.round(longitude.astype(float), 8)
    latitude = np.round(latitude.astype(float), 8)
    return {
        "longitude": np.round(longitude, 8),
        "latitude": np.round(latitude, 8),
        "depth": np.asarray(config.target_depths, dtype=float),
    }


def _interpolate_source(source: _Source, target: Mapping[str, np.ndarray], config: PreprocessConfig) -> xr.Dataset:
    dataset = source.dataset
    # Do not interpolate a coordinate outside a source's valid extent. xarray's
    # default extrapolate=False turns those values into NaN.
    for coordinate in ("latitude", "longitude"):
        if coordinate not in target:
            continue
        if config.regrid_method == "nearest":
            dataset = dataset.interp({coordinate: target[coordinate]}, method="nearest")
        else:
            dataset = dataset.interp({coordinate: target[coordinate]}, method="linear")
    if "depth" in dataset.coords:
        depth_kwargs = {"depth": target["depth"]}
        dataset = dataset.interp(depth_kwargs)
    return dataset


def _concat_variable(parts: Sequence[tuple[str, xr.DataArray]], policy: str) -> tuple[xr.DataArray, list[str]]:
    if not parts:
        raise ValueError("Cannot concatenate an empty variable list")
    if len(parts) == 1:
        return parts[0][1], [parts[0][0]]
    if policy not in {"first", "last"}:
        raise ValueError("duplicate_policy must be 'first' or 'last'")
    units = {str(array.attrs.get("units", "")).strip().lower() for _, array in parts}
    if len(units) > 1:
        detail = ", ".join(f"{name}={array.attrs.get('units', 'unspecified')}" for name, array in parts)
        raise ValueError(f"Incompatible units for {parts[0][1].name}: {detail}")
    # Sources can have different time frequencies (for example daily O2 plus
    # monthly pH). Concatenate and keep the first value on duplicate timestamps.
    arrays = [item[1] for item in parts]
    combined = xr.concat(arrays, dim="time", coords="minimal", compat="override")
    time_values = combined.time.values
    if policy == "first":
        _, chosen_indices = np.unique(time_values, return_index=True)
    else:
        _, reverse_indices = np.unique(time_values[::-1], return_index=True)
        chosen_indices = time_values.size - 1 - reverse_indices
    combined = combined.isel(time=np.sort(chosen_indices)).sortby("time")
    return combined, [item[0] for item in parts]


def _quality_report(dataset: xr.Dataset, source_names: Iterable[str]) -> dict:
    report = {
        "sources": sorted(set(source_names)),
        "dimensions": {key: int(value) for key, value in dataset.sizes.items()},
        "coordinates": {},
        "variables": {},
    }
    for coordinate in ("time", "depth", "latitude", "longitude"):
        if coordinate in dataset.coords:
            values = dataset[coordinate].values
            report["coordinates"][coordinate] = {
                "count": int(values.size),
                "min": str(values.min()) if values.size else None,
                "max": str(values.max()) if values.size else None,
            }
    for name in dataset.data_vars:
        values = dataset[name].values
        finite = np.isfinite(values)
        report["variables"][name] = {
            "units": dataset[name].attrs.get("units", ""),
            "shape": list(values.shape),
            "valid_count": int(finite.sum()),
            "total_count": int(finite.size),
            "missing_fraction": float(1.0 - finite.mean()) if finite.size else 1.0,
            "min": float(np.nanmin(values)) if finite.any() else None,
            "max": float(np.nanmax(values)) if finite.any() else None,
        }
    return report


def preprocess_files(
    input_paths: Sequence[str | Path],
    output_path: str | Path,
    config: PreprocessConfig | None = None,
    report_path: str | Path | None = None,
    source_names: Mapping[str | Path, str] | None = None,
) -> tuple[xr.Dataset, dict]:
    """Preprocess input files and write a common NetCDF dataset plus a report."""
    config = config or PreprocessConfig()
    if not input_paths:
        raise ValueError("At least one input NetCDF file is required")
    sources = []
    for path in input_paths:
        source_name = (source_names.get(path) or source_names.get(str(path))) if source_names else None
        sources.append(open_source(path, source_name=source_name))
    target = _target_coordinates(config, sources)
    source_datasets = [_interpolate_source(source, target, config) for source in sources]

    by_variable: dict[str, list[tuple[str, xr.DataArray]]] = {name: [] for name in CANONICAL_VARIABLES}
    for source, dataset in zip(sources, source_datasets):
        for variable in dataset.data_vars:
            if variable in by_variable:
                by_variable[variable].append((source.name, dataset[variable]))

    output_vars: dict[str, xr.DataArray] = {}
    variable_sources: dict[str, list[str]] = {}
    for variable, parts in by_variable.items():
        if not parts:
            continue
        if len(parts) > 1:
            warnings.warn(
                f"{variable} appears in {len(parts)} source files; combining by time using duplicate_policy={config.duplicate_policy}",
                stacklevel=2,
            )
        data_array, names = _concat_variable(parts, config.duplicate_policy)
        data_array.attrs = dict(data_array.attrs)
        data_array.attrs["source_products"] = ",".join(names)
        output_vars[variable] = data_array
        variable_sources[variable] = list(dict.fromkeys(names))

    if not output_vars:
        raise ValueError("No thetao, o2 or ph variables were found")
    output = xr.Dataset(output_vars).sortby("time")
    output.attrs.update({
        "title": "CMEMS common preprocessing cube",
        "processing": "horizontal regridding, vertical interpolation, missing-value masking, time merge",
        "target_resolution_degree": config.resolution,
        "target_depths_m": ",".join(str(value) for value in config.target_depths),
        "regrid_method": config.regrid_method,
        "vertical_extrapolation": "disabled",
        "source_products": ";".join(sorted({name for names in variable_sources.values() for name in names})),
    })
    report = _quality_report(output, [name for names in variable_sources.values() for name in names])
    report["variable_sources"] = variable_sources
    report["source_inventory"] = []
    for source in sources:
        time_values = source.dataset.time.values
        depth_values = source.dataset.depth.values if "depth" in source.dataset.coords else np.array([])
        report["source_inventory"].append({
            "source": source.name,
            "file": str(source.path),
            "variables": {
                name: {"units": source.dataset[name].attrs.get("units", "")}
                for name in source.dataset.data_vars
            },
            "time_count": int(time_values.size),
            "time_start": str(time_values.min()) if time_values.size else None,
            "time_end": str(time_values.max()) if time_values.size else None,
            "depth_min_m": float(depth_values.min()) if depth_values.size else None,
            "depth_max_m": float(depth_values.max()) if depth_values.size else None,
        })
    report["depth_coverage"] = {
        variable: {
            str(float(level)): int(np.isfinite(data.sel(depth=level, method="nearest").values).sum())
            for level in config.target_depths
        }
        for variable, data in output.data_vars.items()
        if "depth" in data.dims
    }
    report["coverage_warnings"] = [
        f"{variable} at {depth} m has no valid cells; check whether the input depth subset extends below this level."
        for variable, levels in report["depth_coverage"].items()
        for depth, count in levels.items()
        if count == 0
    ]
    report["config"] = {
        "target_depths": list(config.target_depths),
        "bbox": list(config.bbox) if config.bbox else None,
        "resolution": config.resolution,
        "regrid_method": config.regrid_method,
        "duplicate_policy": config.duplicate_policy,
    }
    if config.min_valid_fraction > 0:
        for variable, stats in report["variables"].items():
            valid_fraction = 1.0 - stats["missing_fraction"]
            if valid_fraction < config.min_valid_fraction:
                raise ValueError(
                    f"{variable} valid fraction {valid_fraction:.3f} is below "
                    f"the configured minimum {config.min_valid_fraction:.3f}"
                )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    encoding = {name: {"zlib": True, "complevel": 4, "dtype": "float32"} for name in output.data_vars}
    output.to_netcdf(output_path, encoding=encoding, engine="h5netcdf")
    if report_path is None:
        report_path = output_path.with_suffix(".quality.json")
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return output, report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", nargs="+", required=True, help="Input NetCDF files")
    parser.add_argument("--output", required=True, help="Output unified NetCDF path")
    parser.add_argument("--report", help="Optional quality report JSON path")
    parser.add_argument("--bbox", nargs=4, type=float, metavar=("LON_MIN", "LON_MAX", "LAT_MIN", "LAT_MAX"), required=True)
    parser.add_argument("--resolution", type=float, default=0.25, help="Target horizontal grid spacing in degrees")
    parser.add_argument("--depths", nargs="+", type=float, default=list(DEFAULT_DEPTHS), help="Target physical depths in metres")
    parser.add_argument("--regrid-method", choices=("linear", "nearest"), default="linear")
    parser.add_argument("--duplicate-policy", choices=("first", "last"), default="first", help="Which source wins when timestamps overlap")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    config = PreprocessConfig(
        target_depths=tuple(args.depths),
        bbox=tuple(args.bbox),
        resolution=args.resolution,
        regrid_method=args.regrid_method,
        duplicate_policy=args.duplicate_policy,
    )
    _, report = preprocess_files(args.input, args.output, config=config, report_path=args.report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
