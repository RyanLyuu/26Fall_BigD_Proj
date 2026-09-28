import { create } from 'zustand';
import type { OceanEvent } from '../types';
import events from '../mock/events.json';
interface State { variable:string; dataProduct:string; date:string; depth:number; eventType:string; activeLayer:string; currentEvent:OceanEvent; isRunning:boolean; set:(patch:Partial<State>)=>void; runAnalysis:()=>void; }
export const useAnalysisStore=create<State>((set)=>({variable:'复合事件',dataProduct:'GLORYS12',date:'2024-08-13',depth:20,eventType:'三要素复合事件',activeLayer:'全部事件',currentEvent:events[0] as OceanEvent,isRunning:false,set:(patch)=>set(patch),runAnalysis:()=>{set({isRunning:true});setTimeout(()=>set({isRunning:false}),850)}}));
