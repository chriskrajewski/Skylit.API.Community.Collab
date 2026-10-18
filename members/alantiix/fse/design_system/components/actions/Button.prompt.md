Text button — use for every command; at most one `primary` per view.
```jsx
<Button variant="primary" icon="play">Run backtest</Button>
<Button icon="file-text">Report</Button>
<Button variant="ghost" size="sm" iconRight="chevron-right">Details</Button>
<Button variant="danger" icon="octagon-x">Halt</Button>
```
- Labels are verbs in sentence case ("Run backtest", not "RUN").
- `danger` is a solid alarm-red fill — reserve for halt / flatten / enable live orders. Use `danger-outline` for ordinary destructive actions.
- Press nudges 1px down; no shadows.