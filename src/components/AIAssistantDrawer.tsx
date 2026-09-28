import { Button, Drawer } from 'antd';
import { RobotOutlined, MessageOutlined } from '@ant-design/icons';
import { useState } from 'react';
import { useAnalysisStore } from '../store/useAnalysisStore';
import ai from '../mock/ai.json';
interface Props { open:boolean; onClose:()=>void }
const questions=Object.keys(ai) as (keyof typeof ai)[];
export default function AIAssistantDrawer({open,onClose}:Props){const e=useAnalysisStore(s=>s.currentEvent);const [answer,setAnswer]=useState('');return <Drawer title={<span><RobotOutlined/> AI 研究助手</span>} open={open} onClose={onClose} width={420} className="ai-drawer"><div className="ai-context"><b>回答依据</b><span>{e.dataSources.join('、')} · {e.startTime} 至 {e.endTime} · {e.depthRange[0]}–{e.depthRange[1]} m</span></div><div className="ai-block"><div className="ai-block-title">当前事件总结</div><p>{e.summary} 事件位于 {e.coordinates[0].toFixed(1)}°E、{e.coordinates[1].toFixed(1)}°N，强度在 {e.depthRange[0]}–{e.depthRange[1]} m 范围内最突出。</p></div>{answer&&<div className="ai-answer"><MessageOutlined/><span>{answer}</span></div>}<div className="ai-block-title quick-title">快捷问题</div><div className="questions">{questions.map(q=><Button key={q} onClick={()=>setAnswer(ai[q])}>{q}</Button>)}</div><p className="ai-disclaimer">AI 内容由本地模板生成，仅用于演示分析辅助能力。</p></Drawer>}
