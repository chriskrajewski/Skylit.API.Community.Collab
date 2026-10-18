(function(){
const { Tabs, Panel, Stat, KeyValue, Sparkline, DataTable, MeterBar, Button, IconButton, Badge, CodeBlock } = window.FseDesignSystem_043819;
function Funnel(){const D=window.FSE_DATA;const tot=D.funnel[0][1];
return <div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(380px,1fr))',gap:16,alignItems:'start'}}>
<Panel title="Gate funnel" subtitle={tot+' candidate setups'} icon="filter"><div style={{display:'grid',gap:2}}>{D.funnel.slice(1).map(([g,n])=><MeterBar key={g} label={g} value={n} max={tot} labelWidth={180} tone={n===0?'muted':'ink'} display={n===0?'disabled':n}/>)}<div style={{height:6}}/><MeterBar label="accepted → filled" value={38} max={tot} labelWidth={180} tone="bull"/></div></Panel>
<Panel title="Shadow trades" subtitle="rejected by one Gate only" icon="ghost" flush footer="A shadow trade is simulated as if its only rejecting Gate were off."><DataTable dense rows={D.shadow} columns={[{key:'gate',label:'Gate'},{key:'key',label:'Setup_Key',muted:true},{key:'r',label:'R',align:'right',render:v=><span className={v.startsWith('+')?'fse-pos':'fse-neg'}>{v}</span>}]}/></Panel>
</div>}
function ReportScreen({id,back}){const D=window.FSE_DATA;const run=D.runs.find(r=>r.id===id)||D.runs[0];const [tab,setTab]=React.useState('sum');
return <div>
<PageHead title={run.id} sub={run.cfg+' · '+run.ses+' sessions · 2025-10-06 → 2026-10-02 · seed '+run.seed} actions={<><Button icon="arrow-left" variant="ghost" onClick={back}>Runs</Button><IconButton icon="copy" label="Copy run id" variant="secondary"/><Button icon="file-text">report.md</Button></>}/>
<div style={{padding:'0 24px'}}><Tabs value={tab} onChange={setTab} items={[{value:'sum',label:'Summary'},{value:'trades',label:'Trades',count:run.tr},{value:'funnel',label:'Gate funnel'},{value:'man',label:'Manifest'}]}/></div>
<div style={{padding:'16px 24px 24px',display:'grid',gridTemplateColumns:'minmax(0,1fr)',gap:16}}>
{tab==='sum'&&<>
<div style={{font:'var(--type-small)',color:'var(--text-tertiary)'}}>These figures are measured backtest results on cached data. They are not a forecast of future results.</div>
<div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(150px,1fr))',gap:16}}><Stat size="lg" label="Combine pass" value={run.pass==null?'—':run.pass.toFixed(4)}/><Stat label="Primary win rate" value={run.wr.toFixed(1)} unit="%" sub="95% CI 29.3 – 60.1"/><Stat label="Expectancy" value={(run.ex>0?'+':'')+run.ex.toFixed(2)} unit="R" tone={run.ex>0?'pos':'neg'} sub="95% CI 0.08 – 0.54"/><Stat label="Max drawdown" value={run.dd.toFixed(2)} unit="R" tone="neg"/><Stat label="Trades" value={run.tr} sub={(run.tr/run.ses).toFixed(2)+' per session'}/></div>
<Panel title="Cumulative R" icon="trending-up" subtitle="by trade, net of $1.00/side"><Sparkline values={run.curve} width={1000} height={180} fill responsive tone="auto"/></Panel>
<Panel title="Header"><KeyValue columns={3} items={[['config','playbook_baseline.yaml'],['config hash','3f1a9c…e2'],['bars','1 min · Atlas'],['fills','trade-through 1 tick'],['costs','$1.00 / contract / side'],['holdout','not included'],['sizing','fixed 5 MES / 3 MNQ'],['skipped','2 mixed_roll_session'],['bootstrap','10,000 resamples · seed 1']]}/></Panel></>}
{tab==='trades'&&<Panel flush><DataTable dense rows={D.trades} columns={[{key:'date',label:'Session',muted:true},{key:'time',label:'Entry',muted:true},{key:'inst',label:'Inst'},{key:'pattern',label:'Pattern'},{key:'dir',label:'Dir',render:v=><Badge side={v}/>},{key:'qty',label:'Qty',align:'right'},{key:'entry',label:'Entry',align:'right'},{key:'stop',label:'Stop',align:'right'},{key:'exit',label:'Exit',align:'right'},{key:'r',label:'R',align:'right',render:v=><span className={v.startsWith('+')?'fse-pos':v.startsWith('0')?'fse-flat':'fse-neg'}>{v}</span>}]}/></Panel>}
{tab==='funnel'&&<Funnel/>}
{tab==='man'&&<CodeBlock plain code={JSON.stringify({run_id:run.id,config_id:'playbook_baseline',config_hash:'3f1a9c0b7d…e2',schema_version:1,seed:run.seed,sessions:run.ses,skipped:[{session:'2025-12-15',reason:'mixed_roll_session'},{session:'2026-03-16',reason:'mixed_roll_session'}],data_cache:'~/.skylit-fse/cache',secrets:'[REDACTED]'},null,2)}/>}
</div></div>}
Object.assign(window,{ReportScreen});
})();
