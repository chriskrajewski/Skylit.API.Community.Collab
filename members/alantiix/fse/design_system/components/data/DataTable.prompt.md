The workhorse table — runs, trades, gate funnel, frontier rows. Sticky header, hairline rows.
```jsx
<DataTable dense onRowClick={open} columns={[
  {key:'id',label:'Run',muted:true},{key:'trades',label:'Trades',align:'right'},
  {key:'exp',label:'Exp R',align:'right',render:v=><span className={v>0?'fse-pos':'fse-neg'}>{v}</span>}]}
  rows={runs} />
```
- Selected row gets a 2px inset accent bar on the first cell.