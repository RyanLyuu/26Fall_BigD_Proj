import type { FeatureCollection, LineString, Polygon } from 'geojson';
import type { OceanGridFrame } from '../types';
import preview from '../mock/grid-demo.json';
import { colorFor, eventLayers } from './eventLayers';

export const MIN_GRID_DEGREES = 0.25;

/** Preserve the service's actual cell centers, resolution and missing data. */
export function gridGeoJSON(frame: OceanGridFrame): FeatureCollection<Polygon> {
  if (!Number.isFinite(frame.resolutionDegrees) || frame.resolutionDegrees < MIN_GRID_DEGREES || frame.resolutionDegrees > 180) {
    throw new Error('Grid resolution must be at least 0.25 degrees');
  }
  if (!frame.depthRange.every(Number.isFinite) || frame.depthRange[0] < 0 || frame.depthRange[1] <= frame.depthRange[0]) {
    throw new Error('Invalid depth interval');
  }
  const half = frame.resolutionDegrees / 2;
  return {
    type: 'FeatureCollection',
    features: frame.cells.flatMap((cell, index) => {
      // Unknown values and valid zeros are different. Neither creates an event fill.
      if (!cell.valid || cell.affectedFraction === 0) return [];
      if (!Number.isFinite(cell.longitude) || !Number.isFinite(cell.latitude) ||
          cell.latitude < -90 || cell.latitude > 90 ||
          cell.longitude < -180 || cell.longitude > 180 ||
          (cell.affectedFraction !== null && (!Number.isFinite(cell.affectedFraction) || cell.affectedFraction < 0 || cell.affectedFraction > 1))) {
        throw new Error(`Invalid ocean grid cell ${index}`);
      }
      const west = cell.longitude - half;
      const east = cell.longitude + half;
      const south = Math.max(-90, cell.latitude - half);
      const north = Math.min(90, cell.latitude + half);
      // Split antimeridian cells rather than drawing a polygon across the globe.
      const intervals = west < -180 ? [[west + 360, 180], [-180, east]]
        : east > 180 ? [[west, 180], [-180, east - 360]] : [[west, east]];
      return intervals.map(([left, right]) => ({
        type: 'Feature' as const,
        properties: {
          combination: cell.combination,
          label: eventLayers.find(layer => layer.key === cell.combination)?.label,
          color: colorFor(cell.combination, cell.affectedFraction),
          eventIds: cell.eventIds,
          // A plain string is convenient for MapLibre filter expressions.
          eventKeys: `|${cell.eventIds.join('|')}|`,
          longitude: cell.longitude,
          latitude: cell.latitude,
          fraction: cell.affectedFraction,
        },
        geometry: { type: 'Polygon' as const, coordinates: [[
          [left, south], [right, south], [right, north], [left, north], [left, south],
        ]] },
      }));
    }),
  };
}

/** Synthetic display fixture, independent of service frames and date controls. */
export function demoGrid(): OceanGridFrame {
  return preview as OceanGridFrame;
}

/** Cancel shared cell edges so selection outlines the footprint, not every pixel. */
export function eventBoundary(frame: OceanGridFrame, eventId: string): FeatureCollection<LineString> {
  const edges = new Map<string, number[][]>();
  const data = gridGeoJSON({ ...frame, cells: frame.cells.filter(cell => cell.eventIds.includes(eventId)) });
  for (const feature of data.features) {
    const ring = feature.geometry.coordinates[0];
    for (let i = 0; i < ring.length - 1; i++) {
      const a = ring[i].map(value => Number(value.toFixed(8)));
      const b = ring[i + 1].map(value => Number(value.toFixed(8)));
      const key = [a.join(','), b.join(',')].sort().join(':');
      if (edges.has(key)) edges.delete(key);
      else edges.set(key, [a, b]);
    }
  }
  return { type: 'FeatureCollection', features: [...edges.values()].map(coordinates => ({
    type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates },
  })) };
}
