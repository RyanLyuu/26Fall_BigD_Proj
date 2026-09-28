import { Tabs } from 'antd';
import { EventTimeline, DepthProfile } from './Charts';
export default function AnalysisTabs(){return <section className="analysis-tabs card"><Tabs items={[{key:'time',label:'时间变化',children:<EventTimeline/>},{key:'depth',label:'深度结构',children:<DepthProfile/>},{key:'metrics',label:'事件指标',children:<div className="empty-tab"><b>事件指标对比</b><span>选择多个事件后可在此比较温度、溶解氧和 pH 指标。</span></div>},{key:'sources',label:'数据来源',children:<div className="empty-tab"><b>数据产品</b><span>当前使用 {useSourceText()}，所有结果均为演示数据。</span></div>}]} /></section>}
function useSourceText(){return 'GLORYS12 与 CMEMS 多年 BGC'}
