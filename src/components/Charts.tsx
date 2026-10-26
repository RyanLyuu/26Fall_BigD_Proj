import { useEffect, useMemo, useRef } from 'react';
import * as echarts from 'echarts';
import timeline from '../mock/timeline.json';
import depth from '../mock/depth.json';
import { useAnalysisStore } from '../store/useAnalysisStore';
function Chart({option,className='mini-chart'}:{option:echarts.EChartsOption,className?:string}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const container = ref.current;
    if (!container) return;
    let chart: echarts.ECharts | undefined;
    const resize = () => {
      // Tabs may mount their contents while still hidden.
      if (!container.clientWidth || !container.clientHeight) return;
      if (!chart) {
        chart = echarts.init(container);
        chart.setOption(option);
      } else {
        chart.resize();
      }
    };
    const observer = new ResizeObserver(resize);
    observer.observe(container);
    resize();
    return () => { observer.disconnect(); chart?.dispose(); };
  }, [option]);
  return <div ref={ref} className={className}/>;
}
const axisLabel={color:'#687386',fontSize:10};
export function EventTimeline(){const s=useAnalysisStore();const eventDays=timeline.filter(x=>x.date>=s.currentEvent.startTime.slice(5)&&x.date<=s.currentEvent.endTime.slice(5));const charts=useMemo(()=>[
 {name:'海水温度',unit:'°C',color:'#E64B35',data:timeline.map(x=>x.temperature),min:27,max:32,threshold:29.5,abnormal:'高于阈值'},
 {name:'溶解氧',unit:'μmol/kg',color:'#2389DA',data:timeline.map(x=>x.oxygen),min:130,max:270,threshold:200,abnormal:'低于阈值'},
 {name:'pH',unit:'',color:'#7C4DCE',data:timeline.map(x=>x.ph),min:7.82,max:8.12,threshold:7.96,abnormal:'低于阈值'}],[]);return <div className="timeline-view"><div className="chart-view-header"><div><h3>多要素时间变化</h3><p>{s.currentEvent.eventId} · 阴影区域为当前事件持续时段</p></div><span className="event-period">事件时段 {s.currentEvent.startTime.slice(5)} 至 {s.currentEvent.endTime.slice(5)}</span></div><div className="timeline-grid">{charts.map(item=><div className="small-multiple" key={item.name}><div className="small-title"><b style={{color:item.color}}>{item.name}</b><span>阈值 {item.threshold} {item.unit}</span></div><Chart option={{animation:false,grid:{left:42,right:12,top:9,bottom:23},tooltip:{trigger:'axis',formatter:(params:any)=>{const p=params[0];return `${p.axisValue}<br/>${item.name}：<b>${p.value} ${item.unit}</b><br/>状态：${(item.name==='海水温度'?p.value>item.threshold:p.value<item.threshold)?item.abnormal:'正常'}`}},xAxis:{type:'category',boundaryGap:false,data:timeline.map(x=>x.date),axisLabel,axisLine:{lineStyle:{color:'#d7deea'}}},yAxis:{type:'value',min:item.min,max:item.max,axisLabel,splitLine:{lineStyle:{color:'#edf0f5'}}},series:[{type:'line',data:item.data,smooth:true,symbol:'none',lineStyle:{color:item.color,width:2.2},areaStyle:{color:item.color,opacity:.05},markLine:{symbol:'none',label:{show:false},lineStyle:{color:item.color,type:'dashed',opacity:.65},data:[{yAxis:item.threshold}]},markArea:{silent:true,itemStyle:{color:'rgba(185,28,75,.08)'},data:eventDays.length?[[{xAxis:eventDays[0].date},{xAxis:eventDays[eventDays.length-1].date}]]:[]}}]}}/></div>)}</div></div>}
export function DepthProfile(){const s=useAnalysisStore();const profiles=useMemo(()=>[
 {name:'海水温度',unit:'°C',color:'#E64B35',data:depth.map(x=>[x.temperature,x.depth]),min:28,max:31},
 {name:'溶解氧',unit:'μmol/kg',color:'#2389DA',data:depth.map(x=>[x.oxygen,x.depth]),min:160,max:260},
 {name:'pH',unit:'',color:'#7C4DCE',data:depth.map(x=>[x.ph,x.depth]),min:7.86,max:8.08}],[]);return <div className="depth-view"><div className="chart-view-header"><div><h3>水体垂向结构</h3><p>{s.currentEvent.eventId} · 共用深度轴，深度向下增加</p></div><span className="current-depth">当前深度 {s.depth} m</span></div><div className="profile-grid">{profiles.map((item,index)=><div className="profile-item" key={item.name}><div className="small-title"><b style={{color:item.color}}>{item.name}</b><span>{item.unit}</span></div><Chart option={{animation:false,grid:{left:index===0?34:12,right:8,top:12,bottom:36},tooltip:{trigger:'axis',formatter:(params:any)=>`${params[0].value[1]} m<br/>${item.name}：<b>${params[0].value[0]} ${item.unit}</b>`},xAxis:{type:'value',min:item.min,max:item.max,splitNumber:2,axisLabel,splitLine:{lineStyle:{color:'#edf0f5'}}},yAxis:{type:'value',inverse:true,min:0,max:50,name:index===0?'深度（m）':'',nameTextStyle:{color:'#687386'},axisLabel:index===0?axisLabel:{show:false},splitLine:{lineStyle:{color:'#edf0f5'}}},series:[{type:'line',data:item.data,smooth:true,symbolSize:6,lineStyle:{color:item.color,width:2.4},itemStyle:{color:item.color},markLine:{symbol:'none',label:{show:false},lineStyle:{color:'#172033',type:'dashed'},data:[{yAxis:s.depth}]},markArea:{silent:true,itemStyle:{color:'rgba(185,28,75,.06)'},data:[[{yAxis:20},{yAxis:50}]]}}]}}/></div>)}</div></div>}
