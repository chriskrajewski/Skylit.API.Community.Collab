(function(){
const { Panel, LevelLadder, Segmented, DataTable, Badge, KeyValue, Tooltip } = window.FseDesignSystem_043819;
const CandleChart = window.FseDesignSystem_043819.CandleChart || (()=>null);
function MapScreen(){const D=window.FSE_DATA;const [sym,setSym]=React.useState('ES');const [m,setM]=React.useState('gamma');
const nodes=D.levels[sym].map((l,i)=>({id:i,strike:l.strike,level:l.level.toFixed(2),value:l.value,kind:l.kind,taps:l.kind==='king'?2:l.kind==='gatekeeper'?1:0,state:l.kind==='air'?'empty':i%3===0?'growing':'stable'}));
return <div>
<PageHead title="Map" sub={'Heatseeker '+m+' · '+(sym==='ES'?'SPX → ES':'QQQ, NDXP → NQ')+' · Snapshot asOf 10:34:58 ET'} actions={<><Segmented options={['ES','NQ']} value={sym} onChange={setSym}/><Segmented options={['gamma','vanna']} value={m} onChange={setM}/></>}/>
<div style={{padding:'0 24px 24px',display:'grid',gridTemplateColumns:'minmax(0,1fr)',gap:16}}>
<Panel title={'Price vs '+m+' map'} subtitle="1 min · all nodes" icon="chart-candlestick"><CandleChart height={380} candles={D.bars[sym]} times={D.times} levels={D.levels[sym].map(l=>({price:l.level,kind:l.kind,label:(l.kind==='air'?'Air pocket':l.kind[0].toUpperCase()+l.kind.slice(1))+(l.strike!=='—'?' '+l.strike:'')})).concat([{price:D.spot[sym],kind:'spot',label:'Spot'}])}/></Panel>
<div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(380px,1fr))',gap:16,alignItems:'start'}}>
<Panel title="Levels" subtitle="futures points" icon="layers" flush><LevelLadder levels={D.levels[sym]} spot={D.spot[sym]}/></Panel>
<div style={{display:'grid',gap:16}}>
<Panel title="Regime" icon="activity"><KeyValue columns={2} items={[['regime',<Badge variant="outline">Positive_Gamma</Badge>],['map grade','B'],[<Tooltip content="Gamma King of SPX, SPY, QQQ vs spot"><span style={{borderBottom:'1px dashed var(--fg-4)'}}>trinity</span></Tooltip>,'agree · all above'],['VIX gap','none'],['King flips','1 today'],['stale','no · 2s']]}/></Panel>
<Panel title="Nodes" icon="git-commit-horizontal" flush><DataTable dense rows={nodes} columns={[{key:'kind',label:'Label',render:v=><span style={{color:v==='king'?'var(--level-king)':v==='ceiling'?'var(--level-ceiling)':v==='floor'?'var(--level-floor)':v==='air'?'var(--text-tertiary)':'var(--text-primary)'}}>{v}</span>},{key:'strike',label:'Strike',align:'right',muted:true},{key:'level',label:'Level',align:'right'},{key:'value',label:'|'+m+'| M',align:'right'},{key:'taps',label:'Taps',align:'right'},{key:'state',label:'State',muted:true}]}/></Panel>
</div></div></div></div>}
window.MapScreen=MapScreen;
})();
