"""Regression tests using the real installed Ocetrac, never a tracker stub."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
from shapely.geometry import shape
import xarray as xr

from data_processing.compound_detection import CompoundConfig, detect_compound_events, write_compound_result
from data_processing.event_detection import DetectionConfig, detect_events, detect_variable
from data_processing.evolution import detect_evolution, join_historical_recent
from data_processing.ocetrac_tracking import TrackingConfig
from data_processing.tests.test_compound_detection import abnormal, cube, single


def dated_cube(dates, surface=False):
    ds = cube(len(dates)).assign_coords(time=np.array(dates, dtype='datetime64[ns]'))
    return ds.isel(depth=[0]) if surface else ds


def all_abnormal(ds):
    abnormal(ds, time=slice(None))
    return ds


class OcetracTrackingTests(unittest.TestCase):
    def test_default_is_one_slice_without_five_day_filter(self):
        self.assertEqual(DetectionConfig().min_duration_steps, 1)
        self.assertEqual(CompoundConfig().min_duration_steps, 1)
        ds = cube(1)
        abnormal(ds)
        result = detect_compound_events(ds, single_config=single(1))
        self.assertEqual(len(result.events), 4)
        self.assertEqual(result.events[-1]['tracking']['version'], '0.1.7')
        self.assertEqual(result.events[-1]['duration_steps'], 1)

    def test_moving_surface_anomaly_is_one_event(self):
        ds = cube(3).isel(depth=[0])
        for t in range(3):
            abnormal(ds, time=t, position=(0, 1, t))
        result = detect_compound_events(ds, single_config=single(1))
        self.assertEqual(len(result.events), 4)
        event = result.events[-1]
        self.assertEqual(event['tracking']['module'], 'SurfTrack')
        self.assertEqual(event['duration_steps'], 3)
        self.assertEqual(len(event['trajectory']), 3)
        self.assertLess(event['trajectory'][0]['centroid']['longitude'], event['trajectory'][-1]['centroid']['longitude'])

    def test_volume_movement_with_overlap_and_depth_connectivity(self):
        ds = cube(2)
        for z in range(2):
            for x in (0, 1):
                abnormal(ds, time=0, position=(z, 1, x))
            for x in (1, 2):
                abnormal(ds, time=1, position=(z, 1, x))
        result = detect_compound_events(ds, single_config=single(1))
        self.assertEqual(len(result.events), 4)
        self.assertEqual(result.events[-1]['depth_range_m'], [10., 20.])
        surface = detect_compound_events(ds, single_config=single(1), config=CompoundConfig(tracking=TrackingConfig(mode='surface')))
        self.assertEqual(len(surface.events), 8)

    def test_global_dateline_connects_and_geometry_is_bounded(self):
        for mode in ('surface', 'volume'):
            with self.subTest(mode=mode):
                ds = cube(2).reindex(longitude=[-180., -90., 0., 90.], fill_value=20.)
                ds.o2.values[:] = 210.
                ds.ph.values[:] = 8.1
                for x in (0, 3):
                    abnormal(ds, time=slice(None), position=(0, 1, x))
                result = detect_compound_events(ds, single_config=single(1), config=CompoundConfig(tracking=TrackingConfig(mode=mode)))
                self.assertEqual(len(result.events), 4)
                self.assertTrue(result.events[-1]['tracking']['periodic_longitude'])
                self.assertGreater(abs(result.events[-1]['centroid']['longitude']), 120)
                geom = shape(result.geojson['features'][-1]['geometry'])
                self.assertTrue(geom.is_valid)
                self.assertGreaterEqual(geom.bounds[0], -180)
                self.assertLessEqual(geom.bounds[2], 180)

    def test_surface_motion_across_dateline(self):
        ds = cube(2).isel(depth=[0]).assign_coords(longitude=[-180., -60., 60.])
        abnormal(ds, time=0, position=(0, 1, 2))
        abnormal(ds, time=1, position=(0, 1, 0))
        result = detect_compound_events(ds, single_config=single(1))
        self.assertEqual(len(result.events), 4)
        self.assertEqual(result.events[-1]['duration_steps'], 2)

    def test_regional_edges_and_latitude_are_not_periodic(self):
        ds = cube(1)
        abnormal(ds, time=0, position=(0, 0, 0))
        abnormal(ds, time=0, position=(0, 2, 2))
        result = detect_compound_events(ds, single_config=single(1))
        self.assertEqual(len(result.events), 8)
        self.assertFalse(result.events[-1]['tracking']['periodic_longitude'])
        with self.assertRaisesRegex(ValueError, '360-degree'):
            detect_compound_events(ds, single_config=single(1), config=CompoundConfig(tracking=TrackingConfig(periodic_longitude=True)))

    def test_missing_day_starts_new_event_without_filling(self):
        ds = all_abnormal(cube(7)).isel(time=[0, 1, 2, 4, 5, 6])
        result = detect_compound_events(ds, single_config=single(1))
        self.assertEqual(len(result.events), 8)
        self.assertFalse(bool(result.masks.thetao_o2_ph_event_mask.isel(time=3).any()))
        self.assertFalse(any(link['time'] == '2024-01-05' for link in result.lineage))

    def test_unknown_holes_are_not_morphologically_filled(self):
        ds = cube(1)
        for y in range(3):
            for x in range(3):
                abnormal(ds, time=0, position=(0, y, x))
        ds.ph.values[:, 0, 1, 1] = np.nan
        result = detect_compound_events(ds, single_config=single(1))
        self.assertEqual(int(result.masks.thetao_o2_ph_event_mask[0, 0].sum()), 8)
        self.assertEqual(int(result.masks.thetao_o2_ph_valid_mask[0, 0, 1, 1]), 0)

    def test_merge_and_split_have_explicit_parent_child_edges(self):
        ds = cube(3).reindex(longitude=np.arange(7)*.25+122., fill_value=20.)
        ds.o2.values[:] = 210.
        ds.ph.values[:] = 8.1
        for t, xs in [(0, (0, 1, 5, 6)), (1, tuple(range(7))), (2, (0, 1, 5, 6))]:
            for x in xs:
                abnormal(ds, time=t, position=(0, 1, x))
        result = detect_compound_events(ds, single_config=single(1))
        links = [link for link in result.lineage if link['parent_event_id'].startswith('CE-THETAO_O2_PH-')]
        self.assertEqual(sum(link['relation'] == 'merge' for link in links), 2)
        self.assertEqual(sum(link['relation'] == 'split' for link in links), 2)
        self.assertTrue(any(link['parent_event_id'] != link['child_event_id'] for link in links))
        self.assertTrue(all('O-' in link['parent_node'] for link in links))

    def test_per_snapshot_size_filter_does_not_count_lifespan_as_area(self):
        ds = all_abnormal(cube(7))
        result = detect_compound_events(ds, single_config=single(1), config=CompoundConfig(tracking=TrackingConfig(min_object_cells=2)))
        self.assertEqual(result.events, [])

    def test_single_entry_points_also_use_ocetrac(self):
        ds = all_abnormal(cube(2))
        result = detect_events(ds, ['thetao'], single(1))
        self.assertEqual(len(result.events), 1)
        self.assertEqual(result.events[0]['tracking']['engine'], 'ocetrac')
        mask, _, events, report = detect_variable(ds.thetao, single(1))
        self.assertEqual(int(mask.sum()), 2)
        self.assertEqual(len(events), 1)
        self.assertEqual(report['tracking']['engine'], 'ocetrac')


class MonthlyDailyEvolutionTests(unittest.TestCase):
    def setUp(self):
        self.history = all_abnormal(dated_cube(['2024-01-16', '2024-02-15']))
        self.recent = all_abnormal(dated_cube(['2024-02-28', '2024-02-29', '2024-03-01', '2024-03-02']))

    def test_contiguous_handover_keeps_event_id_and_native_slice_counts(self):
        result = detect_evolution(self.history, self.recent, single_config=single(1))
        self.assertEqual(len(result.events), 4)
        event = result.events[-1]
        self.assertEqual(event['time_span']['slice_counts'], {'daily': 2, 'monthly': 2})
        self.assertEqual(event['duration_steps'], 4)
        self.assertEqual(event['duration_unit'], 'native_slice')
        self.assertEqual(event['time_span']['span_days'], 62)  # leap February
        self.assertEqual(event['end_time'], '2024-03-02')
        self.assertEqual(result.masks.time.size, 4)  # not 62 invented daily values
        self.assertEqual(result.report['time_axis']['gaps'], [])
        cross = [link for link in result.lineage if link['from_scale'] != link['to_scale']]
        self.assertEqual(len(cross), 7)  # three singles plus four compounds
        self.assertTrue(all(link['parent_event_id'] == link['child_event_id'] for link in cross))
        self.assertIsNone(result.report['combinations']['thetao_o2_ph']['statistics']['lmf'])
        self.assertEqual(set(result.report['combinations']['thetao_o2_ph']['statistics']['by_time_scale']), {'monthly', 'daily'})

    def test_surface_handover_uses_same_calendar_rules(self):
        result = detect_evolution(self.history.isel(depth=[0]), self.recent.isel(depth=[0]), single_config=single(1))
        self.assertEqual(len(result.events), 4)
        self.assertEqual(result.events[-1]['tracking']['module'], 'SurfTrack')
        self.assertEqual(result.events[-1]['duration_steps'], 4)

    def test_handover_gap_does_not_connect_array_neighbours(self):
        recent = all_abnormal(dated_cube(['2024-03-05', '2024-03-06']))
        result = detect_evolution(self.history, recent, single_config=single(1))
        self.assertEqual(len(result.events), 8)
        self.assertEqual(result.report['time_axis']['gaps'], [{'start': '2024-03-01', 'end_exclusive': '2024-03-05'}])
        self.assertFalse(any(link['from_scale'] != link['to_scale'] for link in result.lineage))

    def test_monthly_gap_is_unknown_and_breaks_tracking(self):
        history = all_abnormal(dated_cube(['2023-12-01', '2024-02-01']))
        result = detect_evolution(history, self.recent, single_config=single(1))
        self.assertEqual(len(result.events), 8)
        self.assertEqual(int(result.masks.ph_valid_mask.isel(time=1).sum()), 0)

    def test_overlap_is_removed_and_sources_remain_per_time(self):
        self.history.thetao.attrs['source_products'] = 'GLORYS12'
        self.recent.thetao.attrs['source_products'] = 'PHY_ANFC'
        joined = join_historical_recent(self.history, self.recent)
        self.assertEqual(joined.time.size, 4)
        self.assertEqual(joined.thetao_source_product.values.tolist(), ['GLORYS12', 'GLORYS12', 'PHY_ANFC', 'PHY_ANFC'])
        result = detect_evolution(self.history, self.recent, single_config=single(1))
        self.assertEqual(result.events[-1]['data_sources']['thetao'], ['GLORYS12', 'PHY_ANFC'])

    def test_invalid_time_support_cannot_bridge_a_long_gap(self):
        joined = join_historical_recent(self.history, self.recent)
        invalid = joined.assign_coords(time_end=('time', joined.time_end.values.copy()))
        invalid.time_end.values[1] = np.datetime64('2024-03-05')
        with self.assertRaisesRegex(ValueError, 'exactly one calendar'):
            detect_compound_events(invalid, single_config=single(1, frequency='mixed'))

    def test_explicit_cutover_excludes_later_historical_months(self):
        history = all_abnormal(dated_cube(['2024-01-01', '2024-02-01', '2024-03-01']))
        result = detect_evolution(history, self.recent, single_config=single(1), cutover='2024-03-01')
        self.assertEqual(result.events[-1]['time_span']['slice_counts'], {'daily': 2, 'monthly': 2})
        self.assertEqual(result.masks.time.size, 4)

    def test_invalid_cutover_grid_and_duration_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'first day'):
            join_historical_recent(self.history, self.recent, cutover='2024-02-15')
        with self.assertRaisesRegex(ValueError, 'longitude differs'):
            join_historical_recent(self.history, self.recent.assign_coords(longitude=[0., 1., 2.]))
        with self.assertRaisesRegex(ValueError, 'duration=1'):
            detect_evolution(self.history, self.recent, single_config=single(5))

    def test_monthly_and_daily_percentiles_are_not_pooled(self):
        history = dated_cube(['2023-01-01', '2024-01-01'])
        recent = dated_cube(['2024-02-01', '2024-02-02'])
        history.thetao.values[0] = 10.
        history.thetao.values[1] = 20.
        recent.thetao.values[0] = 100.
        recent.thetao.values[1] = 200.
        result = detect_evolution(history, recent, ['thetao'], DetectionConfig())
        self.assertAlmostEqual(float(result.masks.thetao_threshold.sel(time='2024-01-01')[0, 0, 0]), 19.)
        self.assertAlmostEqual(float(result.masks.thetao_threshold.sel(time='2024-02-01')[0, 0, 0]), 190.)

    def test_separate_reference_baselines_and_unavailable_month(self):
        target = all_abnormal(cube(2))
        baseline = cube(10)
        baseline.thetao.values[:] = np.arange(10)[:, None, None, None] + 10.
        result = detect_compound_events(target, ['thetao'], DetectionConfig(), baselines={'daily': baseline})
        self.assertAlmostEqual(float(result.masks.thetao_threshold[0, 0, 0, 0]), 18.1)
        self.assertEqual(len(result.events), 1)
        with self.assertRaisesRegex(ValueError, 'baseline longitude differs'):
            detect_compound_events(target, ['thetao'], DetectionConfig(), baselines={'daily': baseline.assign_coords(longitude=[0., 1., 2.])})
        with self.assertRaisesRegex(ValueError, 'baseline is missing thetao'):
            detect_compound_events(target, ['thetao'], DetectionConfig(), baselines={'daily': baseline[['o2']]})

    def test_mixed_output_roundtrip_preserves_intervals_and_lineage(self):
        result = detect_evolution(self.history, self.recent, single_config=single(1))
        with tempfile.TemporaryDirectory() as directory:
            write_compound_result(result, directory)
            with xr.open_dataset(Path(directory)/'masks.nc') as read:
                self.assertEqual(read.time_scale.values.tolist(), ['monthly', 'monthly', 'daily', 'daily'])
                self.assertEqual(str(read.time_end.values[1])[:10], '2024-03-01')
            links = json.loads((Path(directory)/'lineage.json').read_text())
            self.assertEqual(len(links), len(result.lineage))
            tracks = json.loads((Path(directory)/'tracks.geojson').read_text())
            self.assertEqual(len(tracks['features']), 28)


if __name__ == '__main__':
    unittest.main()
