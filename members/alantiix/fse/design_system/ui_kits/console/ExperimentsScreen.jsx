(function(){
const { Tabs, Panel, DataTable, Badge, Button, Select, Stat } = window.FseDesignSystem_043819;
function ExperimentsScreen(){const D=window.FSE_DATA;const [t,setT]=React.useState('abl');
return <div>
<PageHead title="Experiments" sub="fse experiment · ablation, sweep, pass estimate, walk-forward, cadence, holdout" actions={<Button variant="primary" icon="flask-conical">New experiment</Button>}/>
<div style={{padding:'0 24px'}}><Tabs value={t} onChange={setT} items={[{value:'abl',label:'Ablation'},{value:'sweep',label:'Frontier sweep'},{value:'wf',label:'Walk-forward'}]}/></div>
<div style={{padding:'16px 24px 24px',display:'grid',gridTemplateColumns:'minmax(0,1fr)',gap:16}}>
{t==='abl'&&<><div style={{display:'flex',gap:12,alignItems:'flex-end'}}><div style={{width:260}}><Select label="Variant" options={['− dark_pool_confluence','− tap_count','− trinity_agreement','− candle_color']}/></div><span style={{fontSize:11,color:'var(--text-tertiary)',paddingBottom:8}}>Base bt_8f2c41 · variant bt_77a019 · 248 sessions each</span></div>
<Panel flush title="Base vs variant" icon="split"><DataTable rows={D.ablation} columns={[{key:'m',label:'Metric'},{key:'b',label:'Base',align:'right'},{key:'v',label:'Variant',align:'right'},{key:'d',label:'Change',align:'right',render:v=><span className={v.startsWith('+')?'fse-pos':'fse-neg'}>{v}</span>}]}/></Panel></>}
{t==='sweep'&&<Panel flush title="min_reward_risk.min" subtitle="5 configurations · Pareto set marked" icon="chart-scatter"><DataTable rows={D.frontier} columns={[{key:'rr',label:'Min R:R',align:'right'},{key:'wr',label:'Win %',align:'right'},{key:'tr',label:'Trades',align:'right'},{key:'ex',label:'Exp R',align:'right',render:v=><span className={v>0?'fse-pos':'fse-neg'}>{(v>0?'+':'')+v.toFixed(2)}</span>},{key:'pareto',label:'Pareto',render:v=>v?<Badge variant="accent-soft">Pareto</Badge>:null}]}/></Panel>}
{t==='wf'&&<><div style={{display:'grid',gridTemplateColumns:'repeat(3,minmax(0,1fr))',gap:16}}><Stat label="Out-of-sample exp" value="+0.15" unit="R" tone="pos"/><Stat label="In-sample exp" value="+0.30" unit="R"/><Stat label="No selection" value="1" sub="of 6 windows"/></div>
<Panel flush title="Windows" subtitle="90-day train · 30-day test" icon="calendar-range"><DataTable rows={D.wf} columns={[{key:'w',label:'Window'},{key:'span',label:'Train span',muted:true},{key:'pick',label:'Selected'},{key:'is',label:'IS exp R',align:'right'},{key:'oos',label:'OOS exp R',align:'right',render:v=><span className={v.startsWith('+')?'fse-pos':v==='—'?'fse-flat':'fse-neg'}>{v}</span>}]}/></Panel></>}
</div></div>}
window.ExperimentsScreen=ExperimentsScreen;
})();
