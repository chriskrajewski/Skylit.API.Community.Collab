Modal confirmation — halting the runner, enabling Combine live orders, promoting a config.
```jsx
<Dialog tone="danger" title="Halt the live runner?" onClose={close}
  footer={<><Button variant="ghost" onClick={close}>Cancel</Button><Button variant="danger" icon="octagon-x">Halt and flatten</Button></>}>
  Working orders are cancelled and open positions closed at market.
</Dialog>
```