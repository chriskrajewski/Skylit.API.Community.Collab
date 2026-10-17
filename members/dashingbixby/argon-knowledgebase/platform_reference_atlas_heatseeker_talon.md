# platform_reference_atlas_heatseeker_talon.md

- **Title:** Platform Technical Reference — Heatseeker, Atlas UI v3, Talon Boards & Slash Commands, Nexus & API/MCP
- **Scope:** Complete reference of every UI control, preset, color palette, keyboard shortcut, indicator setting, Talon dashboard board, and Talon slash command across the Skylit platform, including all October 2026 changelog updates.
- **Sources Used:** SRC-01 (Dark Pool Course)[cite: 1], SRC-02 (Atlas Course)[cite: 1], SRC-26 (Talon Prompt Guide, Sec. 1–9)[cite: 1], SRC-29 (Docs Field Guides: Heatseeker, Atlas, Flowseeker, Tempest, Nexus, Talon, API/MCP), SRC-30 (Changelog through Oct 14, 2026).
- **Prerequisites:** `00_index.md`, `glossary.md`
- **Last Updated:** 2026-10-17

---

## 1. Global Terminal Navigation & Keyboard Shortcuts

From the Skylit Field Guides (`skylit.ai/docs/guides/*`) and Changelog (`skylit.ai/changelog`) `[DOCTRINE: Docs Heatseeker & Talon Field Guides; Changelog Oct 13, 2026]`:

| Shortcut / Control | Action | Scope / Notes |
| :--- | :--- | :--- |
| **`Ctrl + K` (`⌘ + K` on Mac)** | Open Command Palette / Search Docs | Searches tickers, commands, and documentation `[DOCTRINE: Docs Overview & Talon Guide]`. |
| **`Shift + S`** | Open Symbol Picker | Opens full-length `All Symbols` list when search is empty `[DOCTRINE: Docs Heatseeker Guide; Changelog Oct 13, 2026]`. |
| **`Shift + H`** | Open / Focus Heatseeker | Switches to or toggles Heatseeker view `[DOCTRINE: Docs Heatseeker Field Guide]`. |
| **`Shift + T`** | Open / Focus Trinity Mode | Opens multi-index Trinity comparison view `[DOCTRINE: Docs Heatseeker Field Guide]`. |
| **`Shift + A`** | Open / Focus Atlas Charting | Switches to Atlas chart view `[DOCTRINE: Docs Heatseeker Field Guide]`. |
| **`Ctrl + Drag` (`⌘ + Drag` on Mac)** | Multi-Select Chart Drawings | With no drawing tool armed on Atlas, selects multiple drawings at once `[DOCTRINE: Changelog Oct 13, 2026]`. |
| **`Mouse Wheel` over Futures Size** | Increment / Decrement Futures Contract Size by `1` | Works over the size stepper, size box, or order buttons on the Futures Ticket; `Ctrl + Wheel` zooms `[DOCTRINE: Changelog Oct 14, 2026]`. |
| **`Options > Text size`** | Scale Application UI & Fonts | Enlarges pages, menus, popovers, and Options (in the Mac desktop app, charts scale too) `[DOCTRINE: Changelog Oct 13, 2026]`. |
| **`Options > Monochrome`** | Monochrome Theme + Viridis Heatmap Scale | Greys chart toolbars, preserves link-group badge colors, and automatically switches heatmaps to the **`Viridis`** color scale `[DOCTRINE: Changelog Oct 13–14, 2026]`. |

---

## 2. Heatseeker Controls, Presets, Palettes & Modes

### 2.1 Header & Toolbar Controls (`[DOCTRINE: Docs Heatseeker Field Guide; Changelog Oct 13, 2026]`)
1. **Symbol Picker (`Shift + S`):** Selects the active optionable stock, ETF, or index (`SPXW`, `SPY`, `QQQ`, `NDX`, `RUT`, `VIX`, and individual equities).
2. **Greek Selector (`GEX` / `VEX`):**
   - `GEX`: Net Gamma Exposure (default for intraday/0DTE)[cite: 1].
   - `VEX`: Net Vanna Exposure (for swing/multi-day context and volatility shifts; can also be enabled on `Scalp` maps where it is labeled as context) `[DOCTRINE: Docs Heatseeker Guide; Changelog Oct 13, 2026]`[cite: 1].
3. **Expiration Selector (`Front` / `Current` up to `5 Expirations`):**
   - `Scalp` maps focus on `Current` (`Front`)[cite: 1].
   - `Swing` and `Position` maps always pin the nearest expiration as an **`"Entry"` column** alongside further expirations `[DOCTRINE: Changelog Oct 13, 2026]`.
4. **Strike Range Depth:** Configurable to **`10`**, **`15`**, **`20`**, **`25`**, **`30`**, or **`50` strikes** around spot price `[DOCTRINE: Docs Heatseeker Field Guide]`.
5. **Velocity Mode (Lightning Icon):**
   - Switches cell values from raw dollar exposure to **percentage rate of change (`% change`)** ("building" vs. "melting")[cite: 1].
   - Configurable lookback intervals: **`1m`**, **`5m`**, **`10m`**, **`15m`**, **`1h`**, **`4h`**, or **`1d`** `[DOCTRINE: Docs Heatseeker Field Guide]`.
6. **Display Presets (Sliders Menu):**
   - **`Off`:** Standard raw exposure view (`$ Value`).
   - **`Focus`:** Displays each node as a percentage of the King Node (`% King`, where King Node = `100%`) and dims minor noise.
   - **`King`:** Isolates the King Node (`KING`, marked with a star) `[DOCTRINE: Docs Heatseeker Field Guide]`.
7. **Color Palettes:**
   - **7 Color Maps available** in settings; **`cividis`** is recommended as the color-blind-safe palette `[DOCTRINE: Docs Heatseeker Field Guide]`. Turning on **Monochrome** switches heatmaps to **`Viridis`** `[DOCTRINE: Changelog Oct 13, 2026]`.

---

## 3. Atlas (UI v3) Chart Overlays, Indicators & Trading Desks

As of October 13, 2026, **UI v3 is the universal Atlas layout** for all members `[DOCTRINE: Changelog Oct 13, 2026]`.

### 3.1 Heatseeker Orb Overlay Controls (`[DOCTRINE: Academy Atlas Course, Sec. 6–8; Changelog Oct 14, 2026]`)
Setting Exposure to `Off` clears both the orbs and the node text labels `[DOCTRINE: Changelog Oct 14, 2026]`. When enabled, three Orb modes exist:
1. **`Orbs (Classic)`:** Simple rendering where color brightness/intensity indicates node value; controls: `Opacity` and `Orb Size` `[DOCTRINE: Academy Atlas Course, Sec. 6]`[cite: 1].
2. **`Orbs V2`:** Granular customization `[DOCTRINE: Academy Atlas Course, Sec. 7]`[cite: 1]:
   - `Orb Size`: Overall zone size[cite: 1].
   - `Min Clamp`: Minimum orb size so minor levels do not vanish[cite: 1].
   - `Max Clamp`: Maximum orb size so dominant nodes do not overwhelm volatile charts[cite: 1].
   - `Min Opacity`: Visibility of weaker nodes[cite: 1].
   - `Max Opacity`: Visibility of major nodes[cite: 1].
   - `King Opacity`: Dedicated visibility control for the single strongest node (`King Node`)[cite: 1].
   - `Scroll as Replay`: Restricts Orbs V2 to only the historical positioning available at that point in time as you scroll backward `[DOCTRINE: Academy Atlas Course, Sec. 7]`[cite: 1].
3. **`Derived Orbs`:** Maps `SPXW/SPY` $\rightarrow$ `ES`, `QQQ` $\rightarrow$ `NQ`, and `SPY` $\leftrightarrow$ `SPXW` with live minute-by-minute adjustment ("the wiggle") `[DOCTRINE: Academy Atlas Course, Sec. 8]`[cite: 1].

### 3.2 Atlas Overlays, Subgraphs & Native Indicators
- **`Dark Pool` Overlay:** Plots `Top N` (`1`, `2`, `3`, or `5`) largest off-exchange blocks over `Lookback` (`30`, `45`, `90`, or `180` days) as horizontal dashed lines (`DP $2.2B · 5/15`) `[DOCTRINE: Academy Dark Pool Course, Sec. 4]`[cite: 1].
- **`Tempest Levels` Overlay:** Plots Expected Move bands with three `Show` options (`Line and label`, `Label with line on hover`, `Line with label on hover`) `[DOCTRINE: Changelog Oct 13, 2026]`.
- **`Auto-hide` by Timeframe (Eye Menu):** Automatically hides `indicators`, `exposure`, or `drawings` at or above a chosen chart interval (`Off`, `5m+`, `15m+`, `30m+`, `1H+`, `4H+`, `1D+`) `[DOCTRINE: Changelog Oct 13, 2026]`.
- **`Initial Balance` Indicator:** Draws `15`, `30`, or `60` minute IB high, low, midpoint, retracements (`golden zone`), and extension ladder for `New York`, `London`, and `Asia` `[DOCTRINE: Changelog Oct 13, 2026]`.
- **`Killzones` Indicator:** Draws session boxes and high/low lines for `ASIA`, `LNDN`, `NYAM`, and `NYPM` on charts `<= 30m` `[DOCTRINE: Changelog Oct 13, 2026]`.
- **`Session Levels` Indicator:** Draws session levels (IB lines were moved out to `Initial Balance` on Oct 13, 2026) `[DOCTRINE: Changelog Oct 13, 2026]`.
- **`Flow` Subgraph Pane:** Plots Call and Put dollar premium bars and `Net` line on the chart timeline, filtered via 7 pill groups `[DOCTRINE: Academy Flowseeker Ch. 8, Sec. 2 & 3]`[cite: 1].
- **Sidecar Heatmaps:** Atlas supports docking up to **10 live Heatseeker heatmaps** beside the chart (`Trinity` defaults to `SPXW`, `SPY`, `QQQ`), with a header close button and `›` edge tab `[DOCTRINE: Docs Atlas Field Guide; Changelog Oct 13, 2026]`.
- **Docked Execution Panels:** `Futures Desk` (with `Trim` 50% button, `BE` break-even stop tag, and mouse-wheel contract sizing), `Options Desk`, `Trade Panel`, and `Trade Deck` `[DOCTRINE: Changelog Oct 13–14, 2026]`.

---

## 4. Talon Dashboard Boards & Talon Chat Reference

### 4.1 Talon Dashboard Ready-Made Boards (Updated Oct 14, 2026)
- As of October 14, 2026, the Talon Dashboard provides **four ready-made workflow boards** alongside `Index & Name` and `Full Board` (replacing the older `Session`, `Heads Up`, and `Pit` tabs) `[DOCTRINE: Changelog Oct 14, 2026]`:
  1. **`Scalp` Board**
  2. **`Swing` Board**
  3. **`Prep` Board**
  4. **`Flow` Board**
- **32 Addable Blocks (Consolidated Oct 14, 2026):** `Atlas` is named **`Chart`**, `Dark Feed` is **`Live Feed`'s `Dark pool tape` view**, `Your Movers` is **`Watchlist`'s `Movers` view**, and `One Gate Short`, `Clustering`, and `Structure Breadth` are views inside **`Market Scan`** `[DOCTRINE: Changelog Oct 14, 2026]`.
- **3-Tab Talon Read Card:** Each Talon symbol read card organizes analysis into three tabs: (1) **`Read`**, (2) **`Hero's Map`**, and (3) **`Bull vs Bear`** (lists nearest 3 levels first + `More`, and states whether a VEX pull is armed) `[DOCTRINE: Changelog Oct 10 & Oct 13, 2026]`.

### 4.2 Talon Chat Context Rules & Slash Commands (`[DOCTRINE: Academy Talon Prompt Guide, Sec. 1, 2, 9]`)
1. **Context Inheritance Rules:**
   - **Rule 1:** The current page and focused ticker ride along automatically with every prompt (`"what are the levels?"` on an NVDA chart resolves to `NVDA`)[cite: 1].
   - **Rule 2:** Naming a ticker explicitly in the prompt **always overrides the page context** (`"levels for MSFT"` while standing on an SPX board fetches `MSFT`)[cite: 1].
   - **Rule 3:** Follow-up questions inherit the **last ticker discussed** in the thread; state the new ticker out loud when switching symbols `[DOCTRINE: Academy Talon Prompt Guide, Sec. 1 & 8]`[cite: 1].
2. **Active vs. Unwired Slash Commands:**

| Slash Command | Wiring Status | Exact Behavior & Scope | Source Tag |
| :--- | :--- | :--- | :--- |
| **`/levels`** | **Wired (Active)** | Returns spot, dominant GEX/VEX nodes, and walls above/below price for the current page's instrument[cite: 1]. | `[DOCTRINE: Talon Prompt Guide, Sec. 2 & 3]`[cite: 1] |
| **`/levels TICKER`** | **Wired (Active)** | Returns key GEX/VEX levels for `TICKER`, overriding page context[cite: 1]. | `[DOCTRINE: Talon Prompt Guide, Sec. 2]`[cite: 1] |
| **`/trinity`** | **Wired (Active)** | Reads all three index books together (`SPXW`, `SPY`, `QQQ`), tagging the ticker on every level, and states whether they agree[cite: 1]. | `[DOCTRINE: Talon Prompt Guide, Sec. 2 & 3]`[cite: 1] |
| **`/talon`** | **Wired (Active)** | Runs a strict-threshold setup scan on the current page's instrument (falls back to structure read if no setup qualifies)[cite: 1]. | `[DOCTRINE: Talon Prompt Guide, Sec. 2 & 7]`[cite: 1] |
| **`/talon TICKER`** | **Wired (Active)** | Runs setup scan on `TICKER`[cite: 1]. | `[DOCTRINE: Talon Prompt Guide, Sec. 2]`[cite: 1] |
| **`/talon sector NAME`** | **Wired (Active)** | Scans curated sector basket + its liquid ETFs: `semiconductors` (`semis`), `energy`, `financials`, `healthcare`, `consumer`, `industrials`[cite: 1]. | `[DOCTRINE: Talon Prompt Guide, Sec. 2]`[cite: 1] |
| **`/talon theme NAME`** | **Wired (Active)** | Scans curated cross-sector theme basket: `ai` (semis + hyperscalers + power), `crypto`, `china`[cite: 1]. | `[DOCTRINE: Talon Prompt Guide, Sec. 2]`[cite: 1] |
| **`/talon market`** | **Wired (Active)** | Scans the index & volatility complex (`SPXW`, `SPY`, `QQQ`, `IWM`, `RUT`, `VIX`, `SMH`, `SOXX`, and leveraged/vol names)[cite: 1]. | `[DOCTRINE: Talon Prompt Guide, Sec. 2]`[cite: 1] |
| **`/clear`** | **Wired (Active)** | Clears chat history and starts a fresh context thread[cite: 1]. | `[DOCTRINE: Talon Prompt Guide, Sec. 2]`[cite: 1] |
| **`/help`** | **Wired (Active)** | Lists terminal slash commands[cite: 1]. | `[DOCTRINE: Talon Prompt Guide, Sec. 2]`[cite: 1] |
| **`/scan`, `/flow`** | **Not Wired as `/` Commands** | Appear in menu but have no `/` handler; **ask in plain English instead** (e.g., `"scan semis"`, `"what's the flow?"`)[cite: 1]. | `[DOCTRINE: Talon Prompt Guide, Sec. 2]`[cite: 1] |
| **`/risk`, `/alert`, `/track`** | **Not Wired (Unsupported)** | Appear in menu but **do nothing today** (no position sizing, alerts, or trade tracking from chat in V1 Beta)[cite: 1]. | `[DOCTRINE: Talon Prompt Guide, Sec. 2 & 9]`[cite: 1] |

3. **Trimmed Toolset on Heatseeker/Trinity Pages:** When Talon Chat is invoked directly from a Heatseeker or Trinity map page, it runs a trimmed toolset for speed—every feature works there **except raw individual prints** (`"show me the prints"`), which must be queried from a Research, Atlas, or Flowseeker page `[DOCTRINE: Academy Talon Prompt Guide, Sec. 9]`[cite: 1].

---

## 5. API / MCP Developer Endpoints

From `skylit.ai/docs/guides/api` and the October 13–14, 2026 Changelog `[DOCTRINE: Docs Overview & API Guide; Changelog Oct 13–14, 2026]`:
- **REST API & MCP Server:** Exposes programmatic access to Heatseeker heatmaps, GEX/VEX nodes, options flow, and Nexus paper trading for custom code and AI agents.
- **Live Option Quotes Route (Added Oct 14, 2026):** `Developer > API reference` provides a `quotes` route returning live `bid`, `ask`, and `mid` by option symbol, and updates open trades in Nexus Trades routes with a live mark (`currentPrice` tracks live mark instead of entry price).
- **Nexus Trades Routes (Added Oct 13, 2026):** Provides endpoints for options, stocks, and futures paper trading, including resting orders, test orders, the dedicated `Agent` bot paper account, wallet reads, and the live trade stream.

---

## Agent Checklist (`platform_reference_atlas_heatseeker_talon.md`)

1. **Resolve Ticker Context Priority:** `Explicit_Prompt_Ticker > Last_Thread_Ticker > Active_Page_Ticker`[cite: 1].
2. **Validate Basket Names on `/talon` Scans:** Only invoke `/talon sector` with `{semiconductors, semis, energy, financials, healthcare, consumer, industrials}` and `/talon theme` with `{ai, crypto, china}`; if asked for an unlisted basket, return the supported list rather than inventing a custom ticker list `[DOCTRINE: Academy Talon Prompt Guide, Sec. 2 & 9]`[cite: 1].
3. **Route Raw Print Queries Away from Map Pages:** If raw option prints are requested while on a Heatseeker/Trinity page context, switch tool scope to the Flowseeker/Atlas endpoint `[DOCTRINE: Academy Talon Prompt Guide, Sec. 9]`[cite: 1].