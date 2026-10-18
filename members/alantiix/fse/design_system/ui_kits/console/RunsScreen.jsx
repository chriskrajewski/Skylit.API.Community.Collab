(function(){
const { Input, Segmented, DataTable, Sparkline, Badge, Button, Panel } = window.FseDesignSystem_043819;
function RunsScreen({openRun}){const D=window.FSE_DATA;const [q,setQ]=React.useState('');const [k,setK]=React.useState('all');
const rows=D.runs.filter(r=>(k==='all'||r.kind===k)&&(r.id+r.cfg).toLowerCase().includes(q.toLowerCase()));
return <div>
<PageHead title="Runs" sub="Backtests and experiments in ~/.skylit-fse/runs · newest first" actions={<Button variant="primary" icon="play">New backtest</Button>}/>
<div style={{padding:'0 24px 24px',display:'grid',gridTemplateColumns:'minmax(0,1fr)',gap:12}}>
<div style={{display:'flex',gap:10,alignItems:'center',flexWrap:'wrap'}}><div style={{width:280}}><Input icon="search" size="sm" placeholder="Filter by run id or config" value={q} onChange={e=>setQ(e.target.value)}/></div><Segmented options={['all','backtest','ablation','sweep','walkforward','cadence']} value={k} onChange={setK}/><div style={{flex:1}}/><span style={{fontSize:11,color:'var(--text-tertiary)'}}>{rows.length} runs</span></div>
<Panel flush style={{minWidth:0}}><DataTable rows={rows} onRowClick={r=>openRun(r.id)} columns={[
{key:'id',label:'Run',render:v=><span style={{color:'var(--text-primary)',fontWeight:500}}>{v}</span>},{key:'cfg',label:'Config',render:v=><span style={{color:'var(--text-secondary)'}}>{v}</span>},{key:'kind',label:'Kind',render:v=><Badge variant="outline">{v}</Badge>},
{key:'ses',label:'Sessions',align:'right'},{key:'tr',label:'Trades',align:'right'},{key:'wr',label:'Win %',align:'right',render:v=>v.toFixed(1)},
{key:'ex',label:'Exp R',align:'right',render:v=><span className={v>0?'fse-pos':'fse-neg'}>{(v>0?'+':'')+v.toFixed(2)}</span>},{key:'pass',label:'Pass p',align:'right',render:v=>v==null?<span style={{color:'var(--text-disabled)'}}>—</span>:v.toFixed(4)},
{key:'curve',label:'Curve',render:v=><Sparkline values={v} width={88} height={16} tone="auto"/>},{key:'date',label:'Finished',muted:true}]}/></Panel>
</div></div>}
window.RunsScreen=RunsScreen;
})();
