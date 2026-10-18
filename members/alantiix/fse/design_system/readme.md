# fse — design system

**fse (futures strategy engine)** is a deterministic AI/ML research, analytics, backtesting and trade-execution engine for MES/MNQ futures, built on the Skylit Heatseeker playbook. It reads ES/NQ levels from Heatseeker GEX/VEX maps and Flowseeker dark-pool prints, plans trades during RTH, and runs the same engine code in two places: a **Backtester** (cached history, no look-ahead, gate funnel, shadow trades, ablation, frontier sweeps, walk-forward, Topstep 50K Combine pass estimates) and a **Live_Runner** (Paper by default; Practice and Combine on ProjectX/TopstepX by explicit opt-in). Every rule is a toggleable Pattern, Gate, Exit_Mode or Kill_Switch in a versioned YAML Strategy_Config.

This system defines the look of the **fse console**, the analytics + operations UI that sits on top of the engine.

## Sources
- Codebase: `skylit-futures-strategy-engine/` (local mount; Python 3.14 CLI, `src/fse/*`). No UI code exists — the console is a new design built from the engine's domain model (Finding_Card fields in `src/fse/notify/finding_card.py`, report structure in `src/fse/reports/markdown.py`, config in `configs/playbook_baseline.yaml`).
- Inspiration: skylit.ai (cousin, not copy), AWS console usability, GitHub file/repo browsing, Apple-level restraint.
- No logo, fonts or imagery were provided.

## Direction (from the brief)
Trading terminal: dense, high-contrast, Bloomberg-ish — but calm and beautiful. Dark first (full light theme). All monospace. Ice-blue brand accent with bull/bear market colors. Density 7/10. Wordmark lockup "fse_ | futures strategy engine".

## CONTENT FUNDAMENTALS
Copy follows the codebase's own voice, which is unusually precise.
- **Plain, exact, declarative.** Short sentences in the present tense. "Paper Order_Mode. No order is sent to ProjectX." "The command prints the run id."
- **No claims, no hype.** The engine "reports measured results and makes no win-rate or profit claim." Never write "alpha", "crush", "AI-powered", "great day". Reports always say: *"These figures are measured backtest results on cached data. They are not a forecast of future results."*
- **No advice, opinion or prediction** (from the Narrator rules). Describe what the data shows now.
- **Missing data is shown, not hidden:** `unavailable` for missing fields, `none` for empty lists, `flat` for no position, `not applicable` for undefined metrics, `—` in dense tables.
- **Domain terms keep their code form** where they are identifiers: `Order_Mode`, `Finding_Card`, `Positive_Gamma`, `A_Plus`, `stale_map`, `min_reward_risk.min`. Config keys are shown verbatim in monospace, never prettified. Human-facing labels use sentence case ("Live runner", "Gate funnel", "Run backtest").
- **Voice:** the system speaks in third person about itself ("The engine closes the position") and addresses the user as "the Operator" in docs; in UI, imperative verbs on buttons, no "I/we", minimal "you".
- **Casing:** sentence case everywhere; UPPERCASE only for micro labels (badges, section labels, table headers) via CSS, never typed. The brand is always lowercase: **fse**.
- **Numbers:** two decimals for prices/R/metrics, four for probabilities (0.1840), signed R (+2.41 R, −1.00 R with a true minus), times in New York ("10:35:00 ET"), dates ISO (2026-10-06).
- **Emoji:** never. Unicode used sparingly as typographic marks only: → for transitions (King 5925 → 5905), ↑ ↓ for watch levels, × for quantity, · as separator, — for empty.

## VISUAL FOUNDATIONS
- **Color:** neutrals run White #ffffff → Mist #e8e8e8 (foreground) → Fog #9ca3af (muted) → Graphite 2 #151515 → Ink #0a0a0a (background) → Black. Surfaces step through an ink ramp (#0a0a0a page, #111 panel, #151515 raised, #1b1b1b hover, #232323 active). **Brand accent is Ice** (#b9d6fc soft / #90bff9 / #5b9df0 deep): primary actions, the King node, focus rings, selection, A+ grades, live status. **Market:** Bull #34d399 for gains/long, Bear #f87171 for losses/short and errors (kill switch, halts, rejected brackets, Combine mode), Warning #fbbf24 for operator-required values and caution, Info = Ice. **Candles:** up = ice blues (body #5e7ca2, wick #7aa2d9, border #90bff9); down = grey body #989898 with white wick and border. **Alarm** (#ef4444, solid fills with white text) is louder than Bear and is reserved for halts, kill switches and live-money confirmations: solid red Halt button in dialogs, hatched full-bleed Banner when halted, red ring + alarm strip on danger dialogs. A losing trade is only ever Bear *text*.
- **Type:** Geist Mono only, 300–700. Body 13px, tables 12px, labels 11px, micro caps 10px with 0.06em tracking. Display/hero numbers go light (300) and large with −0.04em tracking. Tabular numerals globally.
- **Spacing:** 2/4/6/8/12/16/20/24/32/48/64. Controls 24/28/34px; table rows 26 (dense) / 32. Page gutter 24px, panel padding 12px, gaps 16px.
- **Backgrounds:** flat ink. No gradients, no imagery, no textures, no illustrations. The data is the picture.
- **Borders:** 1px hairlines do the structural work (line-1 for panels/rows, line-2 for controls, line-3 for hover). Stat strips use a 1px gap grid over a line-colored background to draw dividers.
- **Shadows:** none on in-page elements. Only overlays (dialog, toast, tooltip-adjacent) get `--shadow-overlay`/`--shadow-pop`: a 1px ring + soft long drop.
- **Corner radii:** small and square: 2px badges, 4px controls, 6px panels/cards/dialogs. Pills only for counts and dots.
- **Cards (Panel):** ink-1 fill, 1px line-1 border, 6px radius, 38px header with title + tertiary subtitle + right-aligned actions, hairline under header. No shadow, no colored left border.
- **Hover:** surfaces step one ink lighter (ink-2 → ink-3); ghost text goes fg-2 → fg-1; borders line-2 → line-3; links turn ice. **Press:** 1px downward nudge; primary darkens to ice-600. **Focus:** 1px page-colored gap + 1px ice ring.
- **Selection:** selected table row = ink-3 + 2px inset ice bar on the first cell. Selected nav item = ink-4 with ice icon. Selected tab = 1px ice underline.
- **Motion:** 80/120/180/280ms, ease-out `cubic-bezier(.2,.7,.2,1)`; no bounce, no spring. Overlays fade + rise 6px. Only the live status dot loops (soft pulse).
- **Transparency & blur:** only the modal scrim (64% ink + 3px blur) and accent washes (10%/18% signal) for soft badges, selection, highlighted code lines.
- **Layout:** fixed 44px top bar (wordmark, repo-style breadcrumb path, ⌘K search, Order_Mode badge), 220px left nav grouped Operate/Research/Configure (AWS-console style), 24px status bar at the bottom (feed health, cache path, config hash, NY clock). Content scrolls; chrome never does.
- **Imagery vibe:** none. If imagery is ever needed, keep it monochrome and cool with ice blue as the only color.
- **Levels:** King = Ice, Ceiling = Bear, Floor = Bull, Gatekeeper = Fog, Air pocket = dim/dotted. Direction badges: long = bull soft, short = bear soft. Practice mode = warning soft. **Alerts** (Alert_2R grade, 2R alert toasts) = Warning. Validation: errors bear, operator-required warning.
- **Charts:** 1.25px strokes, ice for the series, dashed line-2 zero baseline, optional 12% area fill. Candles use the candle tokens. Bars are flat fg-2; the row that matters is ice; disabled is fg-4.

## ICONOGRAPHY
- **Lucide** (outline, 2px stroke on a 24 grid) loaded from CDN (`unpkg.com/lucide-static@0.469.0/icons/<name>.svg`) and inlined as SVG by the `Icon` component (fetched once, cached, stroke = currentColor at 1.75), so icons inherit text color. It's a substitution: the codebase has no icons, and Lucide's thin, geometric stroke suits Geist Mono.
- Sizes: 12 (small controls), 14 (default), 16 (nav/headers). Icons are tertiary/secondary ink by default; signal only when the item is active.
- Common set: radio (live), layers (map), inbox (cards), history (runs), flask-conical (experiments), sliders-horizontal (config), filter (gates), octagon-x (halt), crosshair (position), radar/sunrise (decision/premarket card), play, pause, copy, refresh-cw, search.
- No emoji, no PNG icons, no icon font. Unicode arrows (→ ↑ ↓) and × appear inline in data text only.

## Brand mark
No logo exists. The mark is typographic: **`fse_`** in Geist Mono Bold with the underscore (a terminal cursor) in Ice, then a hairline rule and "futures strategy engine" in fg-2. Use the `Wordmark` component; never draw a symbol.

## Skylit attribution
fse runs on Skylit data (Heatseeker, Flowseeker, Atlas), so the console shows **"Data powered by Skylit.ai API"** via the `PoweredBy` component: in the status bar, and below the lockup on splash/report covers. Rules:
- Type only — no Skylit logo is used. Never redraw it.
- Always secondary to the fse wordmark: tertiary/secondary ink, never Ice.
- The wording is fixed: "Data powered by Skylit.ai API". It describes the data source, not a partnership. Don't shorten it to "Powered by Skylit".

## Index
- `styles.css` — entry point (imports only)
- `tokens/` — fonts, colors (dark + `[data-theme="light"]`), typography, spacing, effects, base
- `components/components.css` — component class styles
- `components/` — React primitives (below), each with `.jsx`, `.d.ts`, `.prompt.md` and a card
- `guidelines/` — foundation specimen cards (Colors, Type, Spacing, Brand)
- `ui_kits/console/` — the fse console click-through (Live runner, Map, Finding cards, Runs, Run report incl. Gate funnel + shadow trades, Experiments, Strategy config)
- `thumbnail.html`, `SKILL.md`

### Components
- **brand/** — Icon, Wordmark, PoweredBy, StatusDot, Kbd
- **actions/** — Button, IconButton
- **forms/** — Input, Select, Checkbox, Switch, Segmented
- **data/** — Badge, Stat, DataTable, KeyValue, Sparkline, MeterBar, LevelLadder, CandleChart
- **navigation/** — Tabs, Breadcrumbs, SideNav
- **surfaces/** — Panel, Dialog, Tooltip, Toast, CodeBlock, Banner
- **domain/** — FindingCard

### Intentional additions
The codebase has no UI, so the whole set is authored from scratch, sized for the console. These are domain-specific and not standard primitives:
- **LevelLadder**: Heatseeker map nodes around spot.
- **FindingCard**: renders the Finding_Card schema.
- **MeterBar**: Gate_Funnel rows.
- **CodeBlock**: Strategy_Config YAML viewer.
- **Icon**: Lucide mask wrapper.
- **Wordmark**: stands in for the missing logo.
- **PoweredBy**: Skylit.ai data attribution.
- **CandleChart**: OHLC with map-level overlays (candle tokens).
- **Banner**: page-level info / warning / alarm strip.

## Caveats
- Font: Geist Mono is loaded from Google Fonts (no font files were provided). Self-host it in `assets/fonts/` for production.
- design.md was not readable; screens are based on the code and README.
