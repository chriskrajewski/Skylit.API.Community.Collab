(function(){
const { FindingCard, Segmented, Badge, Button } = window.FseDesignSystem_043819;
function CardsScreen(){const D=window.FSE_DATA;const [k,setK]=React.useState('all');
const cards=D.cards.filter(c=>k==='all'||(k==='premarket'?c.kind==='premarket':c.kind!=='premarket'));
return <div>
<PageHead title="Finding cards" sub="Sent to console, file and webhook sinks · one card at most per Decision_Time" actions={<><Segmented options={['all','decision','premarket']} value={k} onChange={setK}/><Button icon="external-link">Webhook log</Button></>}/>
<div style={{padding:'0 24px 24px',display:'grid',gap:14,maxWidth:1120}}>
{cards.map((c,i)=><div key={i} style={{display:'grid',gridTemplateColumns:'64px minmax(0,1fr)',gap:14}}><div style={{paddingTop:12,fontSize:11,color:'var(--text-tertiary)',textAlign:'right'}}>{c.time.replace(' ET','')}</div><FindingCard card={c}/></div>)}
</div></div>}
window.CardsScreen=CardsScreen;
})();
