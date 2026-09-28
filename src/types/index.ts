export type EventType = '高温事件' | '低氧事件' | '高酸度事件' | '双要素复合事件' | '三要素复合事件';
export interface OceanEvent { eventId:string; type:EventType; startTime:string; endTime:string; durationDays:number; depthRange:[number,number]; areaKm2:number; temperaturePeak:number; oxygenMinimum:number; phMinimum:number; dataSources:string[]; confidence:string; coordinates:[number,number]; summary:string; }
export interface TimelinePoint { date:string; temperature:number; oxygen:number; ph:number; }
export interface DepthPoint { depth:number; temperature:number; oxygen:number; ph:number; }
