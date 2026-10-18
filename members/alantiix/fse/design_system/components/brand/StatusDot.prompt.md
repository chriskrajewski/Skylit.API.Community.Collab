Small status indicator for runner / feed / broker health.
```jsx
<StatusDot status="live" label="Heatseeker stream" />
<StatusDot status="halted" label="Kill switch" />
```
- Only `live` animates. `halted` is the one place alarm red appears outside dialogs.