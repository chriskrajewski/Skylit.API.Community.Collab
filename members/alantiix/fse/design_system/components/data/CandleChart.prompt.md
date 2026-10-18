Responsive OHLC candle chart with map-level overlays — the price view on Live runner and Map.
```jsx
<CandleChart height={300} candles={bars} levels={[
  {kind:'ceiling',price:5925,label:'Ceiling'},{kind:'king',price:5900,label:'King'},
  {kind:'spot',price:5884.25,label:'Spot'},{kind:'floor',price:5850,label:'Floor'}]} />
```
- Fills container width (ResizeObserver). Candle colors come only from the --color-candle-* tokens.
- King is the only ice line; its axis tag is filled ice.