import type { EventCombination, OceanVariable } from '../types';

// The three pair combinations are distinct classes, matching the backend's
// variable names. The darkest stop represents >=75% of the selected water column.
export const eventLayers: {
  key: EventCombination;
  label: string;
  colors: [string, string, string];
}[] = [
  { key: 'thetao_o2_ph', label: '三要素复合', colors: ['#eeeeee', '#a5a5a5', '#414141'] },
  { key: 'thetao_ph', label: '高温＋高酸度', colors: ['#fde1ed', '#ed93bb', '#8c287e'] },
  { key: 'thetao_o2', label: '高温＋低氧', colors: ['#fee8d0', '#f4ac6b', '#ce641f'] },
  { key: 'o2_ph', label: '低氧＋高酸度', colors: ['#eeebf6', '#b7a4d5', '#70509f'] },
  { key: 'ph', label: '高酸度', colors: ['#e4f2e4', '#9ccea2', '#29964b'] },
  { key: 'o2', label: '低氧', colors: ['#e2f0f7', '#99cbdc', '#327f9e'] },
  { key: 'thetao', label: '高温', colors: ['#fde4dc', '#f2a085', '#d84c32'] },
];

export function combinationFor(variables: OceanVariable[]): EventCombination {
  const key = (['thetao', 'o2', 'ph'] as const).filter(variable => variables.includes(variable)).join('_');
  if (!key) throw new Error('An event combination needs at least one variable');
  return key as EventCombination;
}

export function colorFor(combination: EventCombination, fraction: number | null) {
  const layer = eventLayers.find(layer => layer.key === combination);
  if (!layer) throw new Error(`Unknown event combination: ${combination}`);
  // Missing measurements use the middle categorical color, with a separate UI note.
  if (fraction === null) return layer.colors[1];
  const value = Math.min(0.75, Math.max(0.25, fraction));
  const segment = value < 0.5 ? 0 : 1;
  const mix = (value - (segment === 0 ? 0.25 : 0.5)) / 0.25;
  const from = layer.colors[segment];
  const to = layer.colors[segment + 1];
  return '#' + [1, 3, 5].map(offset => Math.round(
    parseInt(from.slice(offset, offset + 2), 16) * (1 - mix)
    + parseInt(to.slice(offset, offset + 2), 16) * mix,
  ).toString(16).padStart(2, '0')).join('');
}
