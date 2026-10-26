export type EventType = '高温事件' | '低氧事件' | '高酸度事件' | '双要素复合事件' | '三要素复合事件';
export interface OceanEvent { variables:OceanVariable[]; eventId:string; type:EventType; startTime:string; endTime:string; durationDays:number; depthRange:[number,number]; areaKm2:number; temperaturePeak:number; oxygenMinimum:number; phMinimum:number; dataSources:string[]; confidence:string; coordinates:[number,number]; summary:string; }
export interface TimelinePoint { date:string; temperature:number; oxygen:number; ph:number; }
export interface DepthPoint { depth:number; temperature:number; oxygen:number; ph:number; }

export type OceanVariable = 'thetao' | 'o2' | 'ph';
export type EventCombination = 'thetao' | 'o2' | 'ph' | 'thetao_o2' | 'thetao_ph' | 'o2_ph' | 'thetao_o2_ph';

/** Grid centers are provided by the service; their origin must not be snapped. */
export interface OceanGridCell {
  longitude: number;
  latitude: number;
  combination: EventCombination;
  eventIds: string[];
  /** Fraction of the selected depth interval, in [0, 1]; null means unavailable. */
  affectedFraction: number | null;
  valid: boolean;
}

export interface OceanGridFrame {
  source: 'demo' | 'analysis';
  /** ISO date for an actual time slice; null for the synthetic display demonstration. */
  time: string | null;
  resolutionDegrees: number;
  depthRange: [number, number];
  cells: OceanGridCell[];
}
