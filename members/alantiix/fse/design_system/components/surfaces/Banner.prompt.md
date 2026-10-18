Persistent page-level status strip — the loudest thing in fse when tone="alarm" (halted runner, tripped kill switch).
```jsx
<Banner tone="alarm" flush title="Runner halted" actions={<Button size="sm">fse clear</Button>}>Kill switch: daily loss limit. Entries blocked.</Banner>
<Banner tone="warning" title="OPERATOR: required">fills.costs.MNQ.exchange_fee has no value.</Banner>
```
- Alarm is a solid red fill with a subtle hatch so it reads even in peripheral vision. Never use alarm for a losing trade — that's Bear text.