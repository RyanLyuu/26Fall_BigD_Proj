import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from shapely.geometry import shape
import xarray as xr

from data_processing.compound_detection import (
    CompoundConfig, _statistics, detect_compound_events,
    load_compound_inputs, write_compound_result,
)
from data_processing.event_detection import DetectionConfig, _run_filter


def cube(days=12):
    coords = {'time': np.datetime64('2024-01-01') + np.arange(days).astype('timedelta64[D]'),
              'depth': [10., 20.], 'latitude': [27., 27.25, 27.5], 'longitude': [122., 122.25, 122.5]}
    dims = tuple(coords)
    return xr.Dataset({name: xr.DataArray(np.full((days, 2, 3, 3), value), dims=dims, coords=coords,
                                        attrs={'units': unit, 'source_products': 'SYNTHETIC_TEST'})
                       for name, value, unit in [('thetao', 20., 'degrees_C'), ('o2', 210., 'mmol m-3'), ('ph', 8.1, '1')]})


def abnormal(ds, variables=('thetao', 'o2', 'ph'), time=slice(0, 5), position=(0, 0, 0)):
    values = {'thetao': 30., 'o2': 190., 'ph': 7.9}
    for variable in variables:
        ds[variable].values[(time,) + position] = values[variable]


def single(minimum=5, **kwargs):
    return DetectionConfig(threshold_mode='absolute', absolute_thresholds={'thetao': 25., 'o2': 200., 'ph': 8.},
                           min_duration_steps=minimum, **kwargs)


class CompoundDetectionTests(unittest.TestCase):
    def test_inclusive_pairs_and_triple(self):
        ds = cube()
        abnormal(ds)
        result = detect_compound_events(ds, single_config=single())
        self.assertEqual(len(result.events), 4)
        self.assertEqual(len(result.single_events), 3)
        for event in result.events:
            self.assertEqual(event['duration_steps'], 5)
            self.assertEqual(len(event['source_event_ids']), len(event['variables']))
            self.assertAlmostEqual(event['max_volume_km3'] * 5, event['summed_volume_time_km3_steps'])
            self.assertAlmostEqual(event['max_area_km2'], event['footprint_area_km2'])
        self.assertEqual(result.report['combinations']['thetao_o2_ph']['statistics']['joint_count'], 5)

    def test_exclusive_pairs(self):
        ds = cube()
        abnormal(ds)
        abnormal(ds, ('thetao', 'o2'), position=(0, 1, 1))
        result = detect_compound_events(ds, single_config=single(), config=CompoundConfig(exclusive_pairs=True))
        self.assertEqual(len(result.events), 2)
        self.assertEqual(int(result.masks.thetao_o2_event_mask.sum()), 5)
        self.assertEqual(int(result.masks.thetao_o2_ph_event_mask.sum()), 5)

    def test_short_overlap_and_simultaneous_mode(self):
        ds = cube()
        abnormal(ds, ('thetao',), slice(0, 5))
        abnormal(ds, ('o2',), slice(4, 9))
        sustained = detect_compound_events(ds, ['thetao', 'o2'], single(), CompoundConfig(min_duration_steps=5))
        self.assertEqual(sustained.events, [])
        simultaneous = detect_compound_events(ds, ['thetao', 'o2'], single(), CompoundConfig(min_duration_steps=1))
        self.assertEqual(len(simultaneous.events), 1)
        self.assertEqual(simultaneous.events[0]['duration_steps'], 1)

    def test_missing_dates_break_continuity(self):
        ds = cube(7)
        abnormal(ds, time=slice(None))
        ds = ds.isel(time=[0, 1, 2, 4, 5, 6])
        result = detect_compound_events(ds, single_config=single())
        self.assertEqual(result.events, [])
        self.assertEqual(result.masks.time.size, 7)
        self.assertEqual(int(result.masks.ph_valid_mask.isel(time=3).sum()), 0)

    def test_missing_variable_is_error_missing_voxel_is_unknown(self):
        ds = cube()
        abnormal(ds)
        with self.assertRaisesRegex(ValueError, 'Missing'):
            detect_compound_events(ds.drop_vars('ph'), single_config=single())
        ds.ph.values[:, 0, 0, 0] = np.nan
        result = detect_compound_events(ds, single_config=single())
        self.assertEqual(int(result.masks.thetao_o2_ph_valid_mask[:, 0, 0, 0].sum()), 0)
        self.assertEqual(int(result.masks.thetao_o2_ph_event_mask.sum()), 0)
        self.assertEqual(len(result.events), 1)  # temperature+oxygen still has support

    def test_no_common_support_is_distinct_from_no_event(self):
        ds = cube()
        ds.o2.values[:] = np.nan
        result = detect_compound_events(ds, ['o2', 'ph'], single())
        self.assertEqual(result.report['status'], 'no_common_data')
        self.assertIsNone(result.report['combinations']['o2_ph']['statistics']['lmf'])

    def test_same_time_different_depths_do_not_compound(self):
        ds = cube()
        abnormal(ds, ('thetao',), position=(0, 0, 0))
        abnormal(ds, ('o2',), position=(1, 0, 0))
        result = detect_compound_events(ds, ['thetao', 'o2'], single())
        self.assertEqual(result.events, [])

    def test_loader_preserves_input_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'bgc_anfc.nc'
            ds = cube()
            abnormal(ds)
            ds.attrs['product_id'] = 'CMEMS_TEST_PRODUCT'
            for variable in ds:
                ds[variable].attrs.pop('source_products')
            ds.to_netcdf(path, engine='h5netcdf')
            result = detect_compound_events(load_compound_inputs([path]), single_config=single())
            self.assertEqual(result.events[-1]['data_sources']['ph'], 'CMEMS_TEST_PRODUCT')
            self.assertEqual(result.report['input_files'], str(path))

    def test_monthly_guard_and_actual_monthly_detection(self):
        ds = cube(4).assign_coords(time=np.array(['2024-01-16', '2024-02-15', '2024-03-16', '2024-04-16'], dtype='datetime64[ns]'))
        abnormal(ds, time=slice(0, 3))
        with self.assertRaisesRegex(ValueError, 'not daily'):
            detect_compound_events(ds, single_config=single())
        result = detect_compound_events(ds, single_config=single(2, frequency='monthly'), config=CompoundConfig(min_duration_steps=2))
        self.assertEqual(len(result.events), 4)
        self.assertEqual(result.events[-1]['duration_unit'], 'month')
        self.assertEqual(result.events[-1]['duration_steps'], 3)

    def test_daily_data_cannot_be_passed_as_monthly(self):
        with self.assertRaisesRegex(ValueError, 'aggregate to monthly first'):
            detect_compound_events(cube(), single_config=single(1, frequency='monthly'))

    def test_spatial_mismatch_rejected_by_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            a, b = Path(directory)/'a.nc', Path(directory)/'b.nc'
            cube()[['thetao']].to_netcdf(a, engine='h5netcdf')
            cube()[['o2', 'ph']].assign_coords(longitude=[122.1, 122.35, 122.6]).to_netcdf(b, engine='h5netcdf')
            with self.assertRaisesRegex(ValueError, 'longitude differs'):
                load_compound_inputs([a, b])

    def test_dimensions_canonicalized_and_units_checked(self):
        ds = cube().transpose('depth', 'latitude', 'time', 'longitude')
        result = detect_compound_events(ds, single_config=single())
        self.assertEqual(result.masks.thetao_o2_ph_event_mask.dims, ('time', 'depth', 'latitude', 'longitude'))
        ds.o2.attrs['units'] = 'mg/L'
        with self.assertRaisesRegex(ValueError, 'units'):
            detect_compound_events(ds, single_config=single())

    def test_true_footprint_preserves_holes(self):
        ds = cube()
        for y in range(3):
            for x in range(3):
                if (y, x) != (1, 1):
                    abnormal(ds, position=(0, y, x))
        result = detect_compound_events(ds, single_config=single())
        footprint = shape(result.geojson['features'][-1]['geometry'])
        self.assertTrue(footprint.is_valid)
        self.assertEqual(len(footprint.interiors), 1)
        self.assertAlmostEqual(footprint.area, 8 * .25**2)

    def test_gap_merging_cannot_fabricate_compound_overlap(self):
        ds = cube()
        abnormal(ds, ('o2',), slice(0, 5))
        abnormal(ds, ('thetao',), slice(0, 2))
        abnormal(ds, ('thetao',), slice(3, 5))
        result = detect_compound_events(ds, ['thetao', 'o2'], single(gap_tolerance_steps=1), CompoundConfig(min_duration_steps=5))
        self.assertEqual(int(result.masks.thetao_event_span_mask.sum()), 5)
        self.assertEqual(int(result.masks.thetao_event_mask.sum()), 4)
        self.assertEqual(result.events, [])

    def test_gap_filter_does_not_bridge_missing_values_or_long_gaps(self):
        coords = {'time': np.datetime64('2024-01-01') + np.arange(6).astype('timedelta64[D]'), 'depth': [10.]}
        m = xr.DataArray([[1, 1, 0, 0, 1, 1]], dims=('depth', 'time'), coords=coords).astype(bool)
        self.assertFalse(bool(_run_filter(m, single(5, gap_tolerance_steps=1)).any()))
        short = m.copy()
        short.values[0, 3] = True
        valid = xr.ones_like(short, dtype=bool)
        valid.values[0, 2] = False
        self.assertFalse(bool(_run_filter(short, single(5, gap_tolerance_steps=1), valid).any()))
        self.assertTrue(bool(_run_filter(short, single(5, gap_tolerance_steps=1)).all()))

    def test_baseline_absent_month_has_no_valid_threshold(self):
        ds = cube(12)
        ds = ds.assign_coords(time=np.r_[ds.time.values[:6], np.arange(np.datetime64('2024-02-01'), np.datetime64('2024-02-07'))])
        cfg = DetectionConfig(min_duration_steps=1, baseline_start='2024-01-01', baseline_end='2024-01-31')
        result = detect_compound_events(ds, single_config=cfg, config=CompoundConfig(min_duration_steps=1))
        self.assertEqual(int(result.masks.ph_valid_mask.sel(time=slice('2024-02-01', None)).sum()), 0)

    def test_lmf_and_reproducible_block_inference(self):
        states = np.zeros((3, 40, 1, 2, 2), dtype=bool)
        states[:, ::2] = True
        availability = np.ones_like(states)
        cfg = CompoundConfig(block_length=3, bootstrap_repeats=30, permutation_repeats=30)
        stats = _statistics(states, availability, cfg)
        self.assertAlmostEqual(stats['lmf'], 4.)
        self.assertEqual(stats['sample_count'], 160)
        self.assertIsNotNone(stats['ci_95'])
        self.assertIsNotNone(stats['permutation_p_value'])
        self.assertEqual(stats, _statistics(states, availability, cfg))
        short = _statistics(states[:, :2], availability[:, :2], cfg)
        self.assertIsNone(short['ci_95'])
        self.assertIsNone(short['permutation_p_value'])
        self.assertEqual(short['status'], 'insufficient_temporal_samples')
        undefined = _statistics(np.zeros_like(states), availability, cfg)
        self.assertIsNone(undefined['lmf'])
        self.assertEqual(undefined['status'], 'undefined')

    def test_output_roundtrip(self):
        ds = cube()
        abnormal(ds)
        result = detect_compound_events(ds, single_config=single())
        with tempfile.TemporaryDirectory() as directory:
            write_compound_result(result, directory)
            files = {p.name for p in Path(directory).iterdir()}
            self.assertEqual(files, {'masks.nc', 'events.json', 'single_events.json', 'events.geojson', 'quality.json', 'tracks.geojson', 'lineage.json'})
            self.assertEqual(len(json.loads((Path(directory)/'events.json').read_text())), 4)
            with xr.open_dataset(Path(directory)/'masks.nc') as read:
                self.assertEqual(int(read.thetao_o2_ph_event_mask.sum()), 5)
                self.assertIn('thetao_o2_ph_valid_mask', read)


if __name__ == '__main__':
    unittest.main()
