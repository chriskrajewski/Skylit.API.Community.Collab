(function(){
const { Panel, Stat, Button, Badge, LevelLadder, DataTable, FindingCard, Dialog, Segmented, Toast, StatusDot } = window.FseDesignSystem_043819;
const CandleChart = window.FseDesignSystem_043819.CandleChart || (()=>null);
const Banner = window.FseDesignSystem_043819.Banner || (()=>null);
const toLines=(lv,spot)=>lv.filter(l=>l.kind!=='gatekeeper').map(l=>({price:l.level,kind:l.kind,label:l.kind==='air'?'Air pocket':l.kind[0].toUpperCase()+l.kind.slice(1)})).concat([{price:spot,kind:'spot',label:'Spot'}]);
const EV={card:'inbox',order:'arrow-right-left',grade:'star',flip:'repeat',reject:'x',map:'layers',gate:'filter'};
function LiveScreen(){const D=window.FSE_DATA;const [halt,setHalt]=React.useState(false);const [halted,setHalted]=React.useState(false);const [sym,setSym]=React.useState('ES');const [toast,setToast]=React.useState(true);
return <div>
{halted&&<Banner tone="alarm" flush title="Runner halted" actions={<Button size="sm" variant="secondary" icon="rotate-ccw" onClick={()=>setHalted(false)}>fse clear</Button>}>Operator halt at 10:35:12 ET. Working orders cancelled, positions flattened. Entries blocked.</Banner>}
<PageHead title="Live runner" sub="2026-10-06 · RTH · Paper Order_Mode — no order is sent to ProjectX" actions={<>{halted?<Badge variant="danger-solid">Halted</Badge>:<StatusDot status="live" label="Running"/>}<Button icon="pause" disabled={halted}>Pause entries</Button><Button variant="danger-outline" icon="octagon-x" disabled={halted} onClick={()=>setHalt(true)}>Halt</Button></>}/>
<div style={{padding:'0 24px 24px',display:'grid',gridTemplateColumns:'minmax(0,1fr)',gap:16}}>
<div style={{display:'grid',display:'flex',flexWrap:'wrap',border:'1px solid var(--border-subtle)',borderRadius:6,overflow:'hidden',background:'var(--surface-panel)'}}>
{[['Session','+2.41','R','pos','1 closed · 0 open'],['Day P&L','+$903.75',null,'pos','cap $1,200'],['Position','flat',null,'flat','MES · MNQ'],['Working','1',null,null,'MES sell limit 5910.25 ×5'],['Trailing MLL','$48,212',null,null,'floor $47,500 · 50K Combine']].map(([l,v,u,t,s])=><div key={l} style={{flex:'1 1 170px',padding:'14px 16px',boxShadow:'inset -1px -1px 0 var(--border-subtle)',marginRight:-1,marginBottom:-1}}><Stat label={l} value={v} unit={u} tone={t} sub={s}/></div>)}
</div>
<Panel title={sym==='ES'?'MES':'MNQ'} subtitle={'1 min · '+(sym==='ES'?'ES':'NQ')+' levels'} icon="chart-candlestick" actions={<Segmented options={['ES','NQ']} value={sym} onChange={setSym}/>}><CandleChart height={260} candles={D.bars[sym]} times={D.times} levels={toLines(D.levels[sym],D.spot[sym])}/></Panel>
<div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(340px,1fr))',gap:16,alignItems:'start'}}>
<Panel title="Map" subtitle={sym+' · gamma'} icon="layers" flush><LevelLadder levels={D.levels[sym]} spot={D.spot[sym]}/></Panel>
<Panel title="Decision log" icon="list" flush footer="Every decision is written to ~/.skylit-fse/live-state/2026-10-06.jsonl"><DataTable dense rows={D.log} columns={[{key:'t',label:'Time',muted:true,width:72},{key:'ev',label:'Event',width:80,render:v=><span style={{display:'inline-flex',gap:6,alignItems:'center',color:v==='reject'?'var(--text-tertiary)':v==='order'?'var(--text-accent)':v==='grade'?'var(--color-warning)':v==='flip'?'var(--color-ice-soft)':'var(--text-secondary)'}}>{v}</span>},{key:'msg',label:'Detail',render:v=><span style={{whiteSpace:'normal'}}>{v}</span>}]}/></Panel>
<div style={{display:'grid',gap:10}}><div style={{font:'var(--type-micro)',letterSpacing:'var(--ls-caps)',textTransform:'uppercase',color:'var(--text-disabled)'}}>Latest Finding_Card</div><FindingCard card={D.cards[0]}/></div>
</div></div>
{toast&&<div style={{position:'fixed',right:20,bottom:40,zIndex:20}}><Toast tone="warning" icon="bell" title="2R alert · MES floor_ceiling_bounce long" message="Entry 5850.25 · stop 5846.00 · 2.4R · alert only, no order placed" onClose={()=>setToast(false)}/></div>}
<Dialog open={halt} tone="danger" alarmLabel="Paper · affects live runner state" title="Halt the live runner?" onClose={()=>setHalt(false)} footer={<><Button variant="ghost" onClick={()=>setHalt(false)}>Cancel</Button><Button variant="danger" icon="octagon-x" onClick={()=>{setHalted(true);setHalt(false)}}>Halt and flatten</Button></>}>The working MES order is cancelled and any open position is closed at market. New entries stay blocked until <code style={{color:'var(--text-primary)'}}>fse clear</code>.</Dialog>
</div>}
window.LiveScreen=LiveScreen;
})();
