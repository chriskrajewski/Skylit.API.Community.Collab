# fse console UI kit
A click-through version of the fse console, designed from scratch (no UI existed before). It uses the components through the bundle namespace.
- `index.html`: app shell with nav. The last page you opened is saved in localStorage. The theme toggle is in the top bar.
- `Shell.jsx`: TopBar, SideNav, StatusBar, PageHead
- `LiveScreen.jsx`: stats strip, map ladder, decision log, latest Finding_Card, 2R toast, Halt dialog
- `MapScreen.jsx`: ES/NQ and gamma/vanna levels, regime, node table
- `RunsScreen.jsx`: filterable run list → `ReportScreen.jsx` (Summary, Trades, Gate funnel + shadow trades, Manifest)
- `ExperimentsScreen.jsx`: ablation, frontier sweep, walk-forward
- `ConfigScreen.jsx`: file tree + YAML/Form views
- `CardsScreen.jsx`: Finding_Card feed
- `data.js`: mock data using real engine vocabulary
