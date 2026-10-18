(function(){
const { Panel, CodeBlock, Segmented, Switch, Input, Button, Badge, Icon } = window.FseDesignSystem_043819;
const Banner = window.FseDesignSystem_043819.Banner || (()=>null);
const YAML=`schema_version: 1
config_id: playbook_baseline
order_mode: paper # paper | practice | combine

regime:
  min_abs_value: 1000000.0

gates:
  stale_map: {enabled: true} # live map hard rule
  map_grade: {enabled: true}
  midpoint: {enabled: true} # never trade the midpoint
  chart_confluence: {enabled: true}
  dark_pool_confluence: {enabled: false} # confluence only
  tap_count: {enabled: true} # 3rd tap = pass
  min_reward_risk:
    enabled: true
    min: 3.0
    alert_min: 2.0 # 2:1 alerts, ping only

orders:
  flatten_time: "15:55"
  max_open: 1

sizing:
  mode: fixed_contracts
  fixed: {MES: 5, MNQ: 3}`;
const FILES=[['configs',1,'folder'],['playbook_baseline.yaml',2,'file'],['example_strategy.yaml',2,'file'],['calendars',1,'folder'],['economic_events.yaml',2,'file'],['roll_calendar.yaml',2,'file']];
const GATES=['stale_map','map_grade','midpoint','chart_confluence','dark_pool_confluence','stdev_fib_zone','trinity_agreement','tap_count','sloppy_seconds','air_pocket_fade','news_window','vix_gap'];
function ConfigScreen(){const [v,setV]=React.useState('yaml');const [f,setF]=React.useState('playbook_baseline.yaml');
return <div>
<PageHead title={f} sub="Strategy_Config · schema 1 · hash 3f1a9c…e2 · every rule is a toggleable Pattern, Gate, Exit_Mode or Kill_Switch" actions={<><Segmented options={[{value:'yaml',label:'YAML'},{value:'form',label:'Form'}]} value={v} onChange={setV}/><Button icon="check">Validate</Button><Button variant="primary" icon="play">Backtest this</Button></>}/>
<div style={{padding:'0 24px 12px'}}><Banner tone="warning" title="OPERATOR: required">fills.costs.MNQ.exchange_fee has no value. fse backtest exits 2 until it is set.</Banner></div>
<div style={{padding:'0 24px 24px',display:'grid',gridTemplateColumns:'220px minmax(0,1fr)',gap:16,alignItems:'start'}}>
<Panel flush title="Files" icon="folder-tree"><div style={{padding:'6px 0'}}>{FILES.map(([n,d,k])=><button key={n} onClick={()=>k==='file'&&setF(n)} style={{display:'flex',alignItems:'center',gap:8,width:'100%',height:28,padding:'0 12px 0 '+(d*12)+'px',border:0,background:n===f?'var(--surface-active)':'transparent',color:n===f?'var(--text-primary)':'var(--text-secondary)',font:'var(--fw-regular) var(--fs-12)/1 var(--font-mono)',cursor:k==='file'?'pointer':'default',textAlign:'left'}}><Icon name={k==='folder'?'folder':'file-code'} size={13} style={{color:'var(--text-tertiary)'}}/>{n}</button>)}</div></Panel>
{v==='yaml'?<div style={{display:'grid',gap:8}}><div style={{display:'flex',alignItems:'center',gap:10,fontSize:11,color:'var(--text-tertiary)'}}><Badge variant="outline">YAML</Badge><span>27 lines · 1 changed vs main</span></div><CodeBlock code={YAML} highlight={[17]}/></div>:
<div style={{display:'grid',gridTemplateColumns:'minmax(0,1fr) minmax(0,1fr)',gap:16,alignItems:'start'}}>
<Panel title="Gates" subtitle={GATES.length+' of 27'} icon="filter"><div style={{display:'grid',gap:12}}>{GATES.map(g=><Switch key={g} label={g} defaultChecked={!['dark_pool_confluence','stdev_fib_zone'].includes(g)}/>)}</div></Panel>
<div style={{display:'grid',gap:16}}><Panel title="min_reward_risk" icon="scale"><div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12}}><Input label="min" defaultValue="3.0" suffix="R"/><Input label="alert_min" defaultValue="2.0" suffix="R" hint="Ping only, never ordered"/></div></Panel>
<Panel title="orders" icon="clock"><div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12}}><Input label="flatten_time" defaultValue="15:55" suffix="ET"/><Input label="max_open" defaultValue="1"/></div></Panel>
<Panel title="fills.costs" icon="receipt"><div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12}}><Input label="MES exchange_fee" prefix="$" defaultValue="1.00"/><Input label="MNQ exchange_fee" prefix="$" defaultValue="" warning="OPERATOR: required"/></div></Panel></div>
</div>}
</div></div>}
window.ConfigScreen=ConfigScreen;
})();
