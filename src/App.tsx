import { Button, Segmented } from 'antd';
import { DownloadOutlined, RobotOutlined, SettingOutlined, CheckCircleFilled } from '@ant-design/icons';
import { useState } from 'react';
import { useAnalysisStore } from './store/useAnalysisStore';
import ControlPanel from './components/ControlPanel';
import OceanMap from './components/OceanMap';
import EventSummary from './components/EventSummary';
import AnalysisTabs from './components/AnalysisTabs';
import AIAssistantDrawer from './components/AIAssistantDrawer';
export default function App(){const [conditionsOpen,setConditionsOpen]=useState(false);const [aiOpen,setAiOpen]=useState(false);const s=useAnalysisStore();return <div className="app-shell"><header className="topbar"><div className="brand"><div className="brand-mark">◒</div><h1>海洋复合极端事件智能分析系统</h1></div><nav className="top-nav"><a className="active">数据分析</a><a>事件列表</a><a>数据说明</a></nav><div className="top-actions"><span className="data-status">{s.isRunning ? <><span className="loading-dot"/> 正在分析</> : <><CheckCircleFilled/> 数据服务正常</>}</span><Button type="text" icon={<RobotOutlined/>} onClick={()=>setAiOpen(true)}>AI 研究助手</Button><Button icon={<DownloadOutlined/>}>导出</Button></div></header><main className="page"><section className="page-toolbar"><div><h2>海洋复合极端事件分析</h2><p>东海陆架 · 2024 年 8 月 · {s.dataProduct} · 演示数据</p></div><div className="toolbar-actions"><Segmented options={['日','月','年']} defaultValue="日"/><Segmented options={['绝对值','异常值']} defaultValue="绝对值"/><Button type="primary" icon={<SettingOutlined/>} onClick={()=>setConditionsOpen(true)}>设置分析条件</Button></div></section><section className="analysis-grid"><div className="map-column"><OceanMap/></div><EventSummary/></section><AnalysisTabs/></main><footer><span>海洋复合极端事件智能分析系统</span><span>结果均为本地演示数据 · v0.2.0</span></footer><ControlPanel open={conditionsOpen} onClose={()=>setConditionsOpen(false)}/><AIAssistantDrawer open={aiOpen} onClose={()=>setAiOpen(false)}/></div>}
