Vertical price ladder of map nodes (King, Ceiling, Floor, Gatekeepers, Air pockets) around spot.
```jsx
<LevelLadder spot={5884.25} levels={[
  {kind:'ceiling',level:5925,strike:5930,value:820},
  {kind:'king',level:5900,strike:5905,value:1400},
  {kind:'floor',level:5850,strike:5855,value:610}]} />
```
- King = Ice, Ceiling = Bear, Floor = Bull, Gatekeeper = Fog. Air pockets render as dashed empty bars.