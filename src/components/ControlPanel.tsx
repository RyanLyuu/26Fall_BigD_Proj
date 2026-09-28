import { Button, Collapse, DatePicker, Drawer, Select, Slider } from 'antd';
import { PlayCircleOutlined, SettingOutlined } from '@ant-design/icons';
import dayjs from 'dayjs';
import { useAnalysisStore } from '../store/useAnalysisStore';
interface Props { open:boolean; onClose:()=>void }
export default function ControlPanel({open,onClose}:Props){const s=useAnalysisStore(); return <Drawer className="analysis-drawer" title={<span><SettingOutlined/> 设置分析条件</span>} open={open} onClose={onClose} width={420} footer={<Button type="primary" block icon={<PlayCircleOutlined/>} loading={s.isRunning} onClick={s.runAnalysis}>运行分析</Button>}>
 <div className="drawer-intro">选择研究范围与事件判识条件，分析结果会同步更新地图、图表和事件摘要。</div>
 <label>研究区域<Select value="东海陆架" options={[{label:'东海陆架',value:'东海陆架'},{label:'黄海陆架',value:'黄海陆架'}]}/></label>
 <label>时间范围<DatePicker.RangePicker value={[dayjs('2024-08-01'),dayjs('2024-08-31')]} format="YYYY-MM-DD" allowClear={false}/></label>
 <label>分析深度 <strong>{s.depth} m</strong><Slider min={0} max={50} step={10} value={s.depth} onChange={depth=>s.set({depth})} marks={{0:'0',10:'10',20:'20',50:'50'}}/></label>
 <label>分析变量<Select value={s.variable} onChange={variable=>s.set({variable})} options={['温度','溶解氧','pH','复合事件'].map(v=>({label:v,value:v}))}/></label>
 <label>数据产品<Select value={s.dataProduct} onChange={dataProduct=>s.set({dataProduct})} options={['GLORYS12','CMEMS多年BGC','CMEMS全球物理分析预报','CMEMS分析预报BGC'].map(v=>({label:v,value:v}))}/></label>
 <Collapse ghost items={[{key:'advanced',label:'高级参数',children:<><label>事件类型<Select value={s.eventType} onChange={eventType=>s.set({eventType})} options={['高温事件','低氧事件','高酸度事件','双要素复合事件','三要素复合事件'].map(v=>({label:v,value:v}))}/></label><div className="advanced-note">阈值采用本地演示配置，可在接入真实数据后替换。</div></>}]} />
 </Drawer>}
