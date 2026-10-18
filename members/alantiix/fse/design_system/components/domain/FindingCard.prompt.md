One Finding_Card (decision or premarket) — map, setups with Grades and rejection reasons, orders, positions, watch levels.
```jsx
<FindingCard card={{ time:'10:35:00 ET', mode:'paper', regime:'Positive_Gamma', mapGrade:'B', trinity:'agree',
  maps:[{sym:'ES',king:'5900.00',ceiling:'5925.00',floor:'5850.00',spot:'5884.25'}],
  setups:[{grade:'A_Plus',key:'MES gatekeeper_fade short 5910 tap 1'},{grade:'Pass',key:'MES rug long 5850 tap 3',reasons:['tap_count','min_reward_risk']}],
  position:'flat', watchUp:'5900.00', watchDown:'5875.00' }} />
```
- Missing fields render "unavailable"; empty lists render "none" — never hidden.