(function(){
const PoweredBy = window.FseDesignSystem_043819.PoweredBy || (()=>null);
const { Wordmark, Breadcrumbs, Input, StatusDot, Badge, IconButton, SideNav, Kbd } = window.FseDesignSystem_043819;
const NAV=[{title:'Operate',items:[{value:'live',label:'Live runner',icon:'radio',meta:'paper'},{value:'map',label:'Map',icon:'layers'},{value:'cards',label:'Finding cards',icon:'inbox',meta:3}]},{title:'Research',items:[{value:'runs',label:'Runs',icon:'history',meta:7},{value:'experiments',label:'Experiments',icon:'flask-conical'}]},{title:'Configure',items:[{value:'config',label:'Strategy config',icon:'sliders-horizontal'}]}];
function TopBar({crumbs,theme,setTheme}){return <header style={{height:'var(--topbar-h)',display:'flex',alignItems:'center',gap:16,padding:'0 14px',borderBottom:'1px solid var(--border-subtle)',background:'var(--surface-panel)'}}>
<span style={{flex:'none',display:'flex'}}><Wordmark size="sm"/></span><span style={{width:1,height:20,background:'var(--border-default)'}}/>
<div style={{flex:1,minWidth:0,overflow:'hidden'}}><Breadcrumbs items={crumbs} className="fse-crumbs--bar"/></div>
<div style={{width:240,flexShrink:1,minWidth:140,position:'relative'}}><Input icon="search" size="sm" placeholder="Jump to run, config, gate"/><span style={{position:'absolute',right:6,top:3,display:'flex',gap:2}}><Kbd>⌘</Kbd><Kbd>K</Kbd></span></div>
<Badge mode="paper" dot/>
<IconButton icon={theme==='light'?'moon':'sun'} label="Toggle theme" size="sm" onClick={()=>setTheme(theme==='light'?'dark':'light')}/>
</header>}
function StatusBar(){return <footer className="fse-statusbar" style={{height:24,display:'flex',alignItems:'center',gap:18,padding:'0 14px',overflow:'hidden',whiteSpace:'nowrap',borderTop:'1px solid var(--border-subtle)',background:'var(--surface-panel)',fontSize:11,color:'var(--text-tertiary)'}}>
<StatusDot status="live" label="Heatseeker stream"/><StatusDot status="ok" label="Atlas bars"/><span className="sb-lo" style={{display:'contents'}}><StatusDot status="paper" label="Paper broker"/><StatusDot status="idle" label="Narrator off"/></span><div style={{flex:1,minWidth:12}}/><PoweredBy/><span className="sb-lo" style={{width:1,height:12,background:'var(--border-default)',flex:'none'}}/><span className="sb-lo" style={{flex:'none'}}>~/.skylit-fse/</span><span className="sb-lo" style={{flex:'none'}}>config 3f1a9c</span><span style={{color:'var(--text-secondary)',flex:'none'}}>10:35:12 ET</span></footer>}
function Shell({page,setPage,crumbs,theme,setTheme,children}){return <div style={{height:'100vh',display:'grid',gridTemplateRows:'auto 1fr auto'}}>
<TopBar crumbs={crumbs} theme={theme} setTheme={setTheme}/>
<div style={{display:'grid',gridTemplateColumns:'var(--sidenav-w) minmax(0,1fr)',minHeight:0}}>
<aside style={{borderRight:'1px solid var(--border-subtle)',background:'var(--surface-panel)',overflow:'auto'}}><SideNav sections={NAV} value={page} onChange={setPage}/></aside>
<main style={{overflow:'auto',minWidth:0}}>{children}</main></div>
<StatusBar/></div>}
function PageHead({title,sub,actions,children}){return <div style={{display:'flex',alignItems:'flex-end',gap:16,padding:'20px 24px 16px'}}><div style={{flex:1,minWidth:0}}><h1 style={{margin:0,font:'var(--type-h1)',letterSpacing:'var(--ls-heading)'}}>{title}</h1>{sub&&<div style={{marginTop:6,font:'var(--type-small)',color:'var(--text-tertiary)'}}>{sub}</div>}{children}</div>{actions&&<div style={{display:'flex',gap:8}}>{actions}</div>}</div>}
Object.assign(window,{Shell,PageHead});
})();
