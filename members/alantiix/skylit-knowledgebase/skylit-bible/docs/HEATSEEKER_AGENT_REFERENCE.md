<!--
DOCUMENT:  HEATSEEKER AGENT REFERENCE
PRODUCT:   Heatseeker™ by Skylit, Inc.
AUDIENCE:  Claude-family models operating as analysis / tooling agents
PURPOSE:   Give an agent everything needed to (a) call Heatseeker correctly and
           (b) interpret the result the way Skylit intends.
BUILT FROM: 32 knowledge-base files in ./extracted-text/ + docs.skylit.ai
           (retrieved 2026-07-25 via the Mintlify docs MCP at docs.skylit.ai/mcp)
RULE:      Every factual claim below is traceable to a cited source. Nothing is
           inferred from general GEX knowledge. See §20 for the source manifest
           and §0.3 for the evidence-tier system.
-->

# Heatseeker Agent Reference

**Heatseeker™ is a Skylit, Inc. product that visualizes options-dealer positioning per strike.** This document is the operating manual for an AI agent using it.

> **This is not typical GEX.** Heatseeker ships its own node taxonomy (`king`, `gatekeeper`, `pika`, `barney`, `significant`, `normal`), its own live velocity metric, and a documented interpretive method. Generic gamma-exposure intuition will produce wrong answers. Use the vocabulary and rules in this document, not priors.

---

## §0. How an agent should use this document

### 0.1 Reading order by task

| If the task is… | Read |
| --- | --- |
| Call the tool / API correctly | §2, §3 |
| Translate user jargon into API parameters | §4 |
| Interpret a heatmap payload | §5, §7, §8 |
| Explain *why* a level matters | §6 |
| Name a setup | §9 |
| Judge conviction / confluence | §10 |
| Decide if now is a bad time to read the map | §11 |
| Decide if this ticker is even readable | §12 |
| State caveats honestly | §13, §15 |
| Quote a number | §14 (never quote a number that is not in §14) |
| Produce output | §16, §17 |
| Stay inside safe boundaries | §18 |

### 0.2 Six standing rules for the agent

1. **Heatseeker is context, not signal.** Official guidance: *"The key to mastering Heatseeker™ is to use it as **context**, not as a **signal generator**."* Present nodes as levels of interest with probabilities, never as instructions to buy or sell. `[A: /best-practices]`
2. **Absolute value governs, not sign or color.** *"The most important factor is **not** whether a node is positive or negative — nor its color… The **larger** the absolute value, the **stronger** the pull."* Rank by `abs(value)`. `[A: /core-concepts]`
3. **Think in zones, not lines.** Official guidance allows **5–10 points of margin** around high-value nodes on SPX. Never quote a cent-precise reversal. `[A: /common-pitfalls-and-mistakes, /core-concepts]`
4. **Cite the snapshot.** Always surface `asOf`, `spot`, and `meta.metric` from the payload. A heatmap read without a timestamp is unfalsifiable. `[A: mintlify://skills/skylit → Verification Checklist]`
5. **Label the evidence tier.** Skylit's published documentation and community coaching material do not always agree (§15). When they conflict, lead with Tier A and name the disagreement.
6. **Never invent a node.** If the payload has no `king`, say so. Absence of overhead nodes is itself a documented signal (§9.4, "Clear Skies"), not a gap to fill.

### 0.3 Evidence tiers

| Tier | Meaning | Sources | How to use |
| --- | --- | --- | --- |
| **A** | Official Skylit product documentation & API contract | `docs.skylit.ai` pages, `/openapi/openapi.yaml`, `mintlify://skills/skylit` | Authoritative. State plainly. |
| **B** | Official Skylit written curriculum, published under Skylit Docs | Patternpedia (by *Warren* & *John Wicks*), Skylit Docs (by *John Wicks*), *The Greeks Bible — Skylit Edition* | Authoritative for concepts and pattern names. |
| **C** | Community coaching material — **AI-generated analyses of recorded sessions**, not primary transcripts | 10 `BootcampSession*.md`, 6 `Heatseeker_10x`, 2 `Futures_Clinic`, 2 case studies, 2 recaps | Practitioner heuristics. Attribute explicitly ("in bootcamp material…"). Numbers here carry summarization risk — see §0.4. |

`[A: /help-page/written-guides]` confirms authorship of Patternpedia and Skylit Docs. `[A: /help-page/general-information]` names the team: **Glitch** — Heatseeker founder (@Glitch_Trades); **Giul** — approved analyst (@Simply0DTE); **John Wicks** — success team (@The_John_Wicks); **Nicog8** — success team (@nicog8_trading).

### 0.4 Known integrity caveats in the Tier C corpus

An agent quoting Tier C must know these files are second-hand:

- Every session/course file is headed *"Transcript Analysis"* or *"This analysis covers…"*, and names a `.vtt` recording or YouTube URL as its source. They are **derived summaries**, so figures inside them may be paraphrases rather than verbatim quotes.
- **`BootcampSession4_transcript.md` does not exist in this corpus.** Session 5's curriculum recap identifies Session 4's topic as *"Glitch Algorithm & Glitch Dots"*. Any "Glitch Dot" reference in Sessions 7–8 therefore lacks its primary definition here. Treat Glitch Dots as an **out-of-scope adjacent indicator**, not part of Heatseeker.
- **Speech-to-text artifacts** appear throughout. Normalize silently, but never "correct" a number you cannot verify:

| Artifact in corpus | Actual meaning | Where |
| --- | --- | --- |
| "keynote", "key note", "Key Secret" | **king node** | Sessions 2, 3, 6 |
| "Pico node" | **Pika node** | `Heatseeker_Recap_12-13-25` |
| "63.90", "63.85", "63.95" | SPX **6390 / 6385 / 6395** | Session 7 |
| "5,57", "5,52", "5,55" | QQQ **557 / 552 / 555** | Session 8 |
| "64.50" | SPX **6450** | Session 10 |
| "CELIUS" | **CELH** (Celsius Holdings) | Session 6 |
| "Queen node" | Informal name for the 2nd-largest node. **Not in the official `nodeType` enum.** | Session 0 only |
| "Valuary Support", "Pelants", "IWS", "CLU" | Garbled. Do not reconstruct. | Sessions 6, 7, 9 |

---

## §1. What Heatseeker is

**Definition (Tier A).** *"Heatseeker™ was engineered to visualize **dealer positioning** at each strike price for an underlying asset. Each node acts as a **magnet**, influencing price behavior as **support or resistance** depending on its location relative to price."* `[A: /how-to-read-and-use-heatseeker]`

It reveals **dealer exposure per strike per expiration**, rendered as a colored value grid. `[A: /core-concepts]`

### 1.1 The two views

| View | Full name | API `metric` | Documented role |
| --- | --- | --- | --- |
| **GEX** | Gamma Exposure | `gamma` (default) | Short-term precision — immediate floor/ceiling `[C: Heatseeker 103]` |
| **VEX** | Vanna Exposure | `vanna` | Longer-horizon forecasting — where dealers are postured in sessions ahead `[B: Patternpedia/topping-bottoming]` `[C: Heatseeker 103]` |

`[A: /navigating-skylit-web-app]` names these **GEX (Gamma Exposure)** and **VEX (Vanna Exposure)**. Tier C material sometimes glosses VEX as "Volatility Exposure" (`Heatseeker 103`) or "Vanna Exposure" (`SPX_3000_Percent_Crash_Case_Study`) — **use *Vanna* Exposure**, matching both the web app and the API's `vanna` enum. `[A: /navigating-skylit-web-app, /openapi/openapi.yaml]`

### 1.2 The heatmap is organized by expiration — this matters

*"In Heatseeker, gamma exposure is organized by expiration. Each column on the heatmap represents a different contract expiration, such as the current expiration, next week, two weeks out, or later periods."* `[A: /atlas/overview]`

*"The same strike can look different depending on the expiration selected. A major node several expirations out may not be a major node for the current expiration."* `[A: /atlas/overview]`

**Agent consequence:** the API returns a **single net value per strike, summed across expirations**, plus the list of contributing dates in `expirations[]`. `[A: /openapi/openapi.yaml]` The API gives you **no way to split one strike's value by expiration.** When a user asks "is that a this-week node or a next-month node?", say the API cannot separate them and point to `expirations[]` for the contributing set. Do not guess.

### 1.3 Who it is for, and what it is not

Designed for traders of **SPX, SPY, QQQ, /ES, /NQ** and other high-liquidity tickers, though the principles generalize. `[A: /intended-audience]`

Officially it is **NOT**: a shortcut to profitability; a guaranteed win; a replacement for your own strategy; a signal bot. `[A: /intended-audience]`

### 1.4 Position in the Skylit platform

| Product | What it does |
| --- | --- |
| **Heatseeker** | Per-strike dealer-positioning heatmaps (this document) |
| **Flowseeker** | Scored options-flow prints, sweeps, contract analytics |
| **Atlas** | Charting overlay bringing Heatseeker / Flowseeker / dark-pool layers onto the chart |
| **Nexus** | Social trading layer — *documentation "coming soon"* |
| **Agenthub** | Referenced as integrated into Atlas charts |

`[A: /platform/overview, /atlas/overview, /nexus/overview, /flowseeker/overview]`

The documented pairing rationale: *"positioning from Heatseeker, intent from Flowseeker — this is the core reason the two products share one gateway and one key."* `[A: /mcp/examples]`

---

## §2. Access surfaces

> **Beta status (Tier A, as of retrieval).** Every API and MCP page carries: *"**Limited beta** — the Skylit API is in limited beta. Create and manage keys in the Developer tab of the Skylit app."* Surface this if a user reports access problems.

### 2.1 MCP server — the primary surface for an agent

| Property | Value |
| --- | --- |
| Endpoint | `https://mcp.skylit.ai/mcp` |
| Transport | Streamable HTTP — JSON-RPC over `POST`, responses as Server-Sent Events |
| Auth | `Authorization: Bearer <api-key>` **or** `X-API-Key: <api-key>` |
| Scope | Unified gateway over **both** products, one key |
| Cost | Identical to the equivalent REST endpoint; remaining balance returned in each result's `meta` |

`[A: /mcp/overview, /mcp/quickstart]`

**Guardrails on the gateway (Tier A):** a **per-session call budget** and an **in-flight concurrency limit**. Exceeding either returns an error explaining the limit rather than running unbounded. *"Credits are only ever debited for calls that actually reach the data API."* `[A: /mcp/overview]`

#### The two Heatseeker tools

| Tool | Returns | Wraps | Credits |
| --- | --- | --- | --- |
| `heat_heatmap` | Current per-strike gamma/vanna heatmap **+ live velocity**, multi-symbol | `GET /v1/heatmap` | **1** |
| `heat_historical_heatmap` | Replay at a past instant, up to **365 days** back | `GET /v1/historical` | **5** |

`[A: /mcp/tools]`

*"`heat_heatmap` accepts comma-separated `symbols` (e.g. `SPY,SPX,QQQ`) for a single cross-asset call — handy for finding gamma/vanna walls across correlated names at once."* `[A: /mcp/tools]`

**Critical gap:** the SSE stream (`/v1/stream`) is **not exposed as an MCP tool**. The `/mcp/tools` catalog lists only the two `heat_*` tools. Streaming requires direct REST. `[A: /mcp/tools]`

#### Everything else on the gateway is Flowseeker

All remaining tools map to Flowseeker (`flow-api.skylit.ai`); `heat_*` maps to Heatseeker (`api.skylit.ai`). Every tool wraps a REST endpoint **1:1** — same auth, same cost, same JSON. `[A: /mcp/tools]`

Grouped catalog, with credit costs `[A: /mcp/tools]`:

- **Discovery (1 ea):** `flow_search`, `list_active_underlyings`, `expirations`
- **Scores & trades:** `flow_feed` (1), `trade_score` (1), `aggregate_score` (3), `flow_aggregate` (3)
- **Sweeps & momentum (3 ea):** `sweeps`, `flow_momentum`, `flow_baseline`
- **Strike & tide (3 ea):** `flow_strikes`, `flow_tide`, `by_strike`
- **Screeners:** `top_underlyings_daily` (1), `top_underlyings_weekly` (1), `top_contracts_daily` (1), `top_contracts_weekly` (1), `unusual_volume` (3), `unusual_oi` (3)
- **Bull/bear ratios:** `chain_bull_bear` (3), `contract_bull_bear` (1), `chain_ratio` (1), `contract_ratio` (1)
- **Stats / Vol-OI / moneyness (1 ea):** `underlying_stats`, `contract_stats`, `vol_oi`, `moneyness`
- **Chains, charts, RVOL:** `option_chain` (3), `underlying_chart` (3), `contract_chart` (3), `underlying_rvol` (1), `contract_rvol` (1)
- **Market-wide & sector (3 ea):** `market_overview`, `market_tide`, `market_breadth`, `sector_flow`
- **Dark pool:** `dark_pool_trades` (5), `dark_pool_top_prints` (3)
- **Heatseeker:** `heat_heatmap` (1), `heat_historical_heatmap` (5)

**OPRA symbol format** for single-contract tools: `{ticker}__{YYMMDD}{C|P}{strike×1000, 8 digits}` — e.g. `AAPL__260117C00250000`. Discover tickers with `flow_search` and expirations with `expirations` first. `[A: /mcp/tools]`

> **Documented inconsistency:** `/mcp/overview` says *"All 38 tools"*; `/mcp/tools` says *"The server exposes **40 tools**"*; `mintlify://skills/skylit` says 40. **Do not assert a count.** Call `tools/list` and report what the server returns. Recorded in §15.

#### Raw MCP handshake (only needed when driving the server by hand)

```bash
BASE=https://mcp.skylit.ai/mcp
KEY="Bearer $SKYLIT_API_KEY"

# 1. initialize — returns an Mcp-Session-Id header (responses are SSE)
curl -sS "$BASE" -H "Authorization: $KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize",
       "params":{"protocolVersion":"2025-06-18","capabilities":{},
                 "clientInfo":{"name":"curl","version":"0"}}}'

# 2. REQUIRED before any other call. Returns 202, empty body.
curl -sS "$BASE" -H "Authorization: $KEY" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "Mcp-Session-Id: <id-from-step-1>" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 3. list tools    -> {"jsonrpc":"2.0","id":2,"method":"tools/list"}
# 4. call a tool   -> {"jsonrpc":"2.0","id":3,"method":"tools/call",
#                      "params":{"name":"heat_heatmap",
#                                "arguments":{"symbols":"SPY,SPX,QQQ"}}}
```

Three hard requirements, all Tier A `[A: /mcp/quickstart]`:
- `Accept` **must** include `text/event-stream`.
- `notifications/initialized` is **mandatory** — *"skip it and `tools/list` comes back empty."*
- `DELETE` with the `Mcp-Session-Id` header ends a session.

Most MCP clients perform this handshake automatically. `401` = key missing/invalid. `403` = key recognized but the plan does not grant API access. `[A: /mcp/quickstart]`

#### Client configuration

Cursor — `~/.cursor/mcp.json` or `.cursor/mcp.json` `[A: /mcp/quickstart]`:
```json
{ "mcpServers": { "skylit": {
    "url": "https://mcp.skylit.ai/mcp",
    "headers": { "Authorization": "Bearer YOUR_API_KEY" } } } }
```

Claude Desktop — two documented paths `[A: /mcp/quickstart]`:
- **Custom Connector (recommended):** *Settings → Connectors → Add custom connector*, paste `https://mcp.skylit.ai/mcp`, supply the key as a bearer token. Requires a Claude plan that supports custom connectors.
- **Config file via `mcp-remote`** (for stdio-only builds):
```json
{ "mcpServers": { "skylit": {
    "command": "npx",
    "args": ["-y","mcp-remote","https://mcp.skylit.ai/mcp",
             "--header","Authorization: Bearer YOUR_API_KEY"] } } }
```

### 2.2 REST API

Base URL: `https://api.skylit.ai` `[A: /api-reference/introduction]`

| Endpoint | Purpose | Credits |
| --- | --- | --- |
| `GET /v1/heatmap` | Live per-strike heatmap, 1+ symbols, **includes `velocityPct`** | 1 |
| `GET /v1/historical` | Replay nearest a past instant, up to 365 days | 5 |
| `GET /v1/stream` | SSE feed, **one symbol per connection** | 1 on connect, then 1/min open |
| `GET /v1/openapi.json` | The OpenAPI 3.1 document | **0** |

`[A: /api-reference/authentication, /openapi/openapi.yaml]`

```bash
curl "https://api.skylit.ai/v1/heatmap?symbols=SPY&metric=gamma" \
  -H "Authorization: Bearer $SKYLIT_API_KEY"
```

### 2.3 Authentication, credits, rate limits

- **Auth:** bearer on every request; `X-API-Key` also accepted. `[A: /api-reference/authentication]`
- **New accounts seeded with 5,000 credits.** `[A: /api-reference/authentication]`
- Every chargeable response carries **`X-Credits-Remaining: <balance>`**. `[A: /api-reference/authentication]`
- **Rate ceiling: 600 requests/minute**, surfaced via `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`. *"This is runaway protection, not your quota — credit metering does the per-customer accounting."* `[A: /api-reference/authentication]`

| Status | Code | Meaning |
| --- | --- | --- |
| 400 | — | Request validation failed |
| 401 | — | Missing or invalid API key |
| 402 | `insufficient_credits` | Out of credits |
| 403 | `account_suspended` | Suspended; or key revoked/expired; or plan lacks API access |
| 404 | `no_data` | Unknown symbol, or no snapshot at/near the requested instant |
| 429 | — | Rate ceiling hit — back off using `Retry-After` |
| 429 | `stream_limit_reached` | More than 5 concurrent streams |
| 503 | — | Heatmap data temporarily unavailable |

`[A: /api-reference/authentication, /api-reference/heatmap/*, /openapi/openapi.yaml]`

**Key hygiene (Tier A, quote it when relevant):** *"Treat API keys like passwords. Never commit them to source control or paste them into a shared agent prompt. Use environment variables and rotate a key if it leaks."* `[A: /mcp/overview]`

### 2.4 Polling cadence — read this before building a monitor

From the OpenAPI description `[A: /openapi/openapi.yaml]`:

> *"**Resolution.** The heatmap is a **1-second time series**. Endpoints return the single snapshot nearest the requested instant — **poll `/v1/heatmap` every 60-90s** for live, or query `/v1/historical` at any minute boundary for a **1-minute replay grid** (no server-side downsampling needed)."*

- Response carries `Cache-Control: private, max-age=5`. `[A: /openapi/openapi.yaml]`
- `meta.resolution` is a string; the spec's example value is `"1m"`. `[A: /openapi/openapi.yaml]`
- **Do not poll faster than 60s.** You will burn credits for cached data. Use `/v1/stream` for genuinely live monitoring.

### 2.5 SSE stream lifecycle

`GET /v1/stream?symbol=SPY&metric=gamma&maxStrikes=92` — **one symbol per connection.** `[A: /api-reference/heatmap/live-sse-stream-one-symbol-per-connection]`

| Event | Payload / meaning |
| --- | --- |
| `connected` | `{symbol, creditsRemaining}` |
| `initial_data` | Current heatmap snapshot on connect |
| `snapshot_update` | Full heatmap on each change |
| `velocity_update` | Per-strike % change |
| `credits` | `{remaining}` — emitted every minute boundary, after each successful debit |
| `closed` | `{reason: "insufficient_credits" \| "account_suspended" \| "credit_check_failed"}` |
| `reconnect` | `{reason: "max_duration"}` — server recycles the connection after **~1h**. Reconnect to continue. |
| `: keepalive` | Comment every **30s** for proxy keepalive |

- **Pricing:** 1 credit on connect, charged **before** the SSE upgrade — *"an under-funded client gets a clean `402` HTTP response, not a half-open stream"* — then 1 credit per minute open.
- **Concurrency:** up to **5 concurrent streams per customer per pod**; exceeding returns `429 stream_limit_reached`.
- The docs note OpenAPI cannot fully model an event stream and link to `docs/api-credits.md` in the `SkylitAI/skylit-main` repo for the full lifecycle.

### 2.6 Web app

`https://app.skylit.ai`. Access arrives by email after signing up for **Heatseeker Pro**; sign in via Discord or Email. Default landing view is the **SPX Heatmap dashboard**. If the invite has not arrived within 1 hour, DM **@GlitchSPX** on Discord with the email tied to the Whop account. `[A: /navigating-skylit-web-app]`

| Task | Action |
| --- | --- |
| Change ticker | Left/right arrows beside the symbol · click the symbol for a dropdown · type `/<ticker>` (e.g. `/SPXW`) |
| Switch view | GEX / VEX tabs, or **`Ctrl+G` → GEX**, **`Ctrl+V` → VEX** (docs specify **Windows**) |
| Support | DM **@GlitchSPX** on Discord |

`[A: /navigating-skylit-web-app]`

> Tier C (`Heatseeker 103`) reports `Command+G` / `Command+V`. Consistent with a macOS equivalent, but only the Windows bindings are documented. State both, attribute each.

**Official learning resources** `[A: /resources]`: YouTube **@Glitch_SPX** (Heatseeker breakdowns and recaps), **@nicog8_trading** (swing recaps and case studies), **@The_John_Wicks** (futures and 0DTE case studies — *"currently in development"*).

---

## §3. Data model — the contract

### 3.1 Request parameters

| Param | Endpoints | Required | Type | Default | Notes |
| --- | --- | --- | --- | --- | --- |
| `symbols` | heatmap, historical | ✅ | string | — | One ticker, or comma-separated for one cross-asset call (`SPY,SPX,QQQ`). Each returns as an element of `data.symbols`. |
| `symbol` | stream | ✅ | string | — | **Singular.** One symbol per connection. |
| `at` | historical | ✅ | RFC3339 | — | Instant to replay, e.g. `2026-03-05T10:01:00Z`. Up to 365 days back. |
| `metric` | all | ❌ | enum `gamma` \| `vanna` | `gamma` | Which Greek exposure to return. |
| `maxStrikes` | all | ❌ | integer | `92` | Max strikes around spot. **min 1, max 400.** |

`[A: /openapi/openapi.yaml]` — the min/max bounds on `maxStrikes` appear only in the raw spec, not the rendered API pages.

### 3.2 Response envelope

Success → `{ "data": …, "meta": { … } }`. Errors → `{ "error": { "code": …, "message": … } }`. **camelCase throughout.** `data.symbols` is **always an array**, so single-symbol and multi-symbol ("Trinity") calls share one shape. `[A: /openapi/openapi.yaml]`

```json
{
  "data": { "symbols": [ {
    "symbol": "SPY",
    "asOf": "2026-05-22T14:31:00Z",
    "spot": 591.23,
    "previousClose": 589.10,
    "priceChange": 2.13,
    "priceChangePercent": 0.36,
    "expirations": ["2026-05-22", "2026-05-23", "2026-05-30"],
    "strikes": [
      { "strike": 590, "value": 1894300.4, "nodeType": "king",       "velocityPct": 12.4 },
      { "strike": 595, "value": 642100.2,  "nodeType": "gatekeeper", "velocityPct": -3.1 },
      { "strike": 585, "value": 88010,     "nodeType": "normal",     "velocityPct": 0.4  }
    ] } ] },
  "meta": { "metric": "gamma", "resolution": "1m", "mode": "live", "cached": false }
}
```

#### `SymbolHeatmap` — all fields required

| Field | Type | Meaning |
| --- | --- | --- |
| `symbol` | string | Canonical ticker for the returned data |
| `asOf` | date-time | RFC3339 timestamp of the snapshot **actually returned** (nearest the requested instant) |
| `spot` | number | Spot price at the snapshot |
| `previousClose` | number | Prior close |
| `priceChange` | number | Spot minus previous close |
| `priceChangePercent` | number | Same, as a percentage |
| `expirations` | date[] | Expiration dates (`YYYY-MM-DD`) **contributing to each strike's net value** |
| `strikes` | StrikeNode[] | Per-strike nodes, **ordered by strike ascending** |

#### `StrikeNode`

| Field | Required | Meaning |
| --- | --- | --- |
| `strike` | ✅ | Strike price |
| `value` | ✅ | **Net** exposure for the selected `metric` at this strike, **summed across the returned expirations** |
| `nodeType` | ✅ | Skylit's classification — enum, see §3.3 |
| `velocityPct` | ❌ | Live % change of this strike's value over the velocity window. **Present on `/v1/heatmap` only; omitted on `/v1/historical`.** |

#### `Meta`

| Field | Type | Meaning |
| --- | --- | --- |
| `metric` | `gamma` \| `vanna` | Which view you got — **always echo this** |
| `resolution` | string | e.g. `"1m"` |
| `mode` | `live` \| `historical` | Which endpoint served it |
| `cached` | boolean | True if served from the in-process cache |

`[A: /openapi/openapi.yaml, /api-reference/heatmap/*]`

### 3.3 The `nodeType` enum — canonical semantics

**`king` · `gatekeeper` · `pika` · `barney` · `significant` · `normal`** — *"the same vocabulary used throughout Patternpedia."* `[A: /api-reference/introduction, /openapi/openapi.yaml]`

| `nodeType` | Definition | Documented behavior |
| --- | --- | --- |
| **`king`** | **Highest absolute value** on the map. Where MMs hold greatest exposure and *"typically prefer to **settle price** by the **end of day (EOD)** or **end of week (EOW)** for swing trades."* | Primary destination target. Watch early-day **drive-offs** and late-day **pinning**. |
| **`gatekeeper`** | A defensive level between price and the King. *"Act like **bouncers at the door of a nightclub**."* | Strong rejection zone. A failed test can trigger **map reshuffle** and a trend change. Early-day gatekeeper rejections *"often mark **high-probability reversals**."* |
| **`pika`** | **Positive** gamma exposure. Bright yellow. | *"Absorbs price movement… tend to reduce volatility. Think of yellow as a **'magnetic pillow'** — price often slows down or gets pinned here."* Also usable as a reversal zone. |
| **`barney`** | **Negative** gamma exposure. Dark purple. | *"Amplifies price movement… Think of purple as **'pouring gasoline on a fire'** — once price enters, it can spike or drop quickly."* Also usable as a reversal zone. |
| **`significant`** | Mid-range value nodes. | Secondary levels of interest. |
| **`normal`** | Low-value nodes. | Minimal influence. |

`[A: /core-concepts` (positive/negative behavior, King, Gatekeeper)`; /introduction` ("magnetic pillow", "gasoline on a fire")`; /how-to-read-and-use-heatseeker` (King as EOD/EOW settlement target)`; mintlify://skills/skylit` (significant, normal)`]`

**Two things an agent must get right:**

1. **`king` is orthogonal to color.** *"Is a King Node Always Yellow? **No — King Nodes are not defined by color.** They can appear as **yellow or purple**… The **largest magnitude** (regardless of color) is considered the King Node."* `[A: /faqs]`
2. **`pika` and `barney` encode sign; `king`/`gatekeeper` encode structural role.** The enum is single-valued, so a strike that is both the largest node *and* negative-gamma will surface as `king` — the sign is then only recoverable from `value`'s arithmetic sign. **Always check `sign(value)` in addition to `nodeType`.**

**Documented node priority order** `[A: /faqs]`:
1. **King Nodes** → dealer settlement targets, highest-probability zones
2. **Gatekeeper Nodes** → deflection zones where trend shifts begin
3. **Clusters of Nodes** → *"when multiple large values group together, price often **pins** or **chops** around that region"*
4. **Double Stacked Nodes** → *"price can have a strong bounce from those levels"*

### 3.4 What the API does *not* give you

Be explicit about these limits rather than improvising. All derived from the schema `[A: /openapi/openapi.yaml]`:

| Not available | Consequence for the agent |
| --- | --- |
| Per-expiration breakdown of a strike's value | Cannot answer "which expiry drives this node". Report `expirations[]` as the contributing set. |
| Colors / hex values | Colors are a **UI rendering** of sign and magnitude. Derive: `value > 0` → green→yellow; `value < 0` → blue→purple. `[A: /core-concepts]` |
| `velocityPct` on historical | Cannot compute velocity for replay. Instead call `/v1/historical` at two instants and difference the values yourself. |
| The velocity **window length** | `velocityPct` is *"over the velocity window"* — the duration is undocumented. **Never state a window length.** Tier C references a *"10-minute rate of increase"* `[C: SPX_3000_Percent_Crash_Case_Study]` — attribute, do not present as spec. |
| Units for `value` | The spec says only "net exposure". Tier C speaks in millions and in "digits" (§12). Treat `value` as **comparable within one symbol's map**, not across symbols. |
| VIX, OI, volume, IV, price bars | Out of scope. Use Flowseeker tools (`option_chain`, `underlying_chart`, `vol_oi`) or another source. |
| Thresholds behind `nodeType` | Skylit does not publish the classification cut-offs. Do not reverse-engineer and present as fact. |

---

## §4. Vocabulary → API translation

Practitioner language in this corpus rarely matches API field names. Use this table before every call.

| User says | Means in the API | Action |
| --- | --- | --- |
| "GEX", "gamma map", "the heatmap" | `metric=gamma` | Default. |
| "VEX", "vanna map", "vol exposure" | `metric=vanna` | Second call — one metric per call. |
| "Trinity", "the trinity view", "all three indices" | `symbols=SPX,SPY,QQQ` | One call, 1 credit. `[A: /mcp/tools]` |
| "king node", "keynote", "Key Secret" | `nodeType == "king"`, or `max(abs(value))` | §0.4 artifact table. |
| "gatekeeper", "GK" | `nodeType == "gatekeeper"` | |
| "yellow", "pika", "pico", "positive node" | `nodeType == "pika"` or `value > 0` | |
| "purple", "barney", "negative node" | `nodeType == "barney"` or `value < 0` | |
| "the floor" | Largest-`abs(value)` node **below** `spot` | Not an API field — compute it. |
| "the ceiling" | Largest-`abs(value)` node **above** `spot` | Not an API field — compute it. |
| "put floor" / "call ceiling" | Same as floor/ceiling. Tier C phrasing. | The API does not label puts vs calls. |
| "velocity", "rate of change", "node growing" | `velocityPct` | **Live only.** |
| "the map reshuffled" | Structural change between two snapshots | Requires ≥2 calls to detect. §8.2. |
| "air pocket", "void zone", "empty basement" | A strike span with only `normal`/low-`abs(value)` nodes | Compute. §5.6. |
| "queen node" | 2nd-largest `abs(value)` | **Informal, not in the enum.** §0.4. |
| "replay the open on March 5" | `heat_historical_heatmap` with `at=2026-03-05T14:30:00Z` | Note the docs' own example uses **UTC** for a US market open. `[A: /mcp/examples]` |
| "0DTE SPX" | Ticker `SPXW` | Docs' shortcut example is `/SPXW`. `[A: /navigating-skylit-web-app]` |
| "how far can it overshoot" | Not in the API | §5.9 margins. |

**Timezone discipline.** `asOf` and `at` are RFC3339. The docs' example maps *"the open on March 5"* to `2026-03-05T14:30:00Z` `[A: /mcp/examples]`. All market-clock guidance in this corpus is quoted in **ET** (documented as "EST" in `/core-concepts`). Convert explicitly and show your work; never assume the user's local zone.

---

## §5. Core interpretive model

### 5.1 Nodes as magnets

*"Every node on the Heatseeker map acts like a **magnet** in the market. Price is **attracted** to these zones due to dealer positioning — yet these same areas can also act as **walls** that repel price and create reversals."* `[A: /core-concepts]`

Documented magnetic behavior `[A: /core-concepts, /how-to-read-and-use-heatseeker]`:
- Price **farther** from a high-value node → pull **weakens**
- Price **approaching** a high-value node → pull **strengthens**
- Direct interaction → *"a **deflection or repulsion** may occur — similar to when two positive ends of a magnet meet and push apart"*

Tier C formalizes the corollary: when two nodes have roughly equal `abs(value)` on either side of spot, treat the **midpoint** as the conservative target rather than the larger node — traders lose money fixating on one node while ignoring equal-and-opposite pull. `[C: Session 5, Session 6]`

### 5.2 Absolute value primacy

*"The most important factor is **not** whether a node is positive or negative — nor its color. What truly matters is the **absolute value** of the node. The **larger** the absolute value, the **stronger** the pull it exerts on price."* `[A: /core-concepts]`

Tier C restates this as the **Absolute Value Rule**: *"A -80M node is a stronger magnet/wall than a +20M node."* `[C: Heatseeker 101]`

**Agent rule:** rank by `abs(value)` descending. Use sign only to predict *character* (§5.3), never *rank*.

### 5.3 Sign and color → volatility character

| Exposure | Color range | Documented behavior |
| --- | --- | --- |
| **Positive** (`pika`) | Green → Yellow | *"Lower-volatility interaction"*; price moves **smoothly**, fewer wicks or spikes |
| **Negative** (`barney`) | Blue → Purple | *"Higher-volatility, 'wicky' movement"*; price becomes **wicky and more violent** |

`[A: /core-concepts]`

The overshoot mechanic, verbatim: *"When price interacts with a **negative gamma node**, it can **overshoot** before reversing — this is how market makers often **trap retail traders** on the wrong side of the move."* `[A: /core-concepts]`

Three documented day-shapes `[A: /introduction]`:
- **Trending day** — price moving smoothly toward a major node with no strong opposing node → *"be prepared for an expansion in volatility. These are moments where momentum trading works best."*
- **Choppy / range-bound** — price trapped within yellow zones → *"expect low volatility and lots of fake outs. This is ideal for premium-selling strategies or reversal strategies, such as fading the edges of the range."*
- **High-volatility interaction** — price approaching purple zones with speed → *"Once inside, expect fast moves."* E.g. a major purple node below spot implies *"a potentially aggressive bounce there."*

Tier C adds framing worth quoting when explaining character, not magnitude: yellow is *"suppressive — price slows down and softens as it approaches"*; purple is *"oppressive/volatile, 'fuel on fire' — price moves aggressively into these nodes."* `[C: Heatseeker 101]` Session 9 adds that yellow is *"more deflective, and it kind of tapers off as the price approaches it, whereas a purple one amplifies moves as it heads towards"* it. `[C: Session 9]`

### 5.4 King node behavior

Two documented modes `[A: /core-concepts]`:

1. **Pin Jobs — common near end of day.** *"MMs often pin price near the King Node late in the session. Tight ranges form. **Scalping the range edges** tends to work best."*
2. **Drives Away — common early in the day.** *"When price reaches the King Node **too early**, MMs may push it away. Holding price there all session would require constant defense, so they often trigger an early **drive-off**."*

Tier C's version of the same idea: *"Markets rarely pin a King Node for 9 hours straight. An open above the King Node suggests a high probability of a 'fade' back through it."* `[C: SPX_3000_Percent_Crash_Case_Study]`

### 5.5 Midpoints are the documented trap

*"**Midpoints** are Market Makers' favorite **trap zones** — and they offer the **worst risk-to-reward** for directional traders… At best, your **risk-to-reward** is roughly **1:1** — not asymmetric and not worth taking."* `[A: /core-concepts]`

*"Unless you're an **option or spread seller**, avoid them."* `[A: /core-concepts]`

The framework's own aphorism: *"**Edge fading** offers asymmetric risk-to-reward. **Midpoint trading** offers symmetry — and symmetry kills edge."* `[A: /how-to-read-and-use-heatseeker]`

Tier B reinforces this for the Whipsaw pattern: *"It is strongly advisable to avoid initiating a position in the middle of the range, especially with 0DTE options, as theta decay and the unstable behavior of price can rapidly erode premiums."* `[B: Patternpedia/pattern-the-whipsaw]`

### 5.6 Air pockets

*"Air Pockets occur when maps show a zone of low volume and/or small sized nodes that price can move easily through due to the lack of resistance/activity within that zone."* `[A: /core-concepts]`

Node quality changes the transit `[A: /core-concepts]`:
- Through a **negative gamma** air pocket → *"much more violent / sharp"*
- Through a **positive gamma** air pocket → *"much more mild / slow"*

The explicit caveat, emphasized in the source: ***"Just because we have an air pocket does not ALWAYS mean we will fly through it, rather its something to keep in mind when analyzing trinity mode. Confluence matters."*** `[A: /core-concepts]`

Tier C names the same structure "void zone" / *"free-fall mode"* / *"liquidity void"* `[C: SPX_3000_Percent_Crash_Case_Study]`, and the inverse — an empty span *below* a floor on SPY/QQQ — the **"Empty Basement"**, read as evidence the floor holds `[C: QQQ_SPY_Floor_Bounce_Recap]`.

**How to compute one:** scan `strikes` ascending; flag any contiguous span between two high-`abs(value)` nodes containing only `normal`/`significant` nodes with small `abs(value)`. There is no API field for this.

### 5.7 Hedge nodes

*"**Hedge Nodes** appear during **major news or macro events** — such as FOMC, CPI, JOLTS, NFP, or earnings. They represent **large, protective positions** that sit **farther from price** and move **slowly** throughout the day."* `[A: /core-concepts]`

Characteristics `[A: /core-concepts]`:
- Typically **static or slow-unwinding**
- Can appear **above and below price simultaneously**, typically far from spot
- *"Function like **insurance** rather than active magnets"*
- **Closer** to price → more it shapes intraday behavior; **farther** → less influence
- *"Watch for **gradual unwinds** of large hedge nodes — they often signal changing Market Maker expectations."*

Tier C detection heuristics — **useful, unverified against Tier A**:
- *"Very round numbers (e.g. 6,200, 6,250) on SPX often indicate dealer hedges, not dealer positioning."* Cross-check against the SPY map, *"because SPX is often hedged."* `[C: Session 5]`
- *"Hedges often static across multiple updates; directional nodes shuffle more frequently."* `[C: Session 5]`
- Large hedge nodes *"often precede news events."* `[C: SPX_3000_Percent_Crash_Case_Study]`
- During Quad Witching / OpEx, massive deep-OTM nodes are *"often Hedge Nodes, not price targets… Take OpEx nodes with a 'grain of salt.'"* `[C: Heatseeker 102]`

### 5.8 OPEX nodes

*"Every **third Friday** of the month marks **OPEX Week**."* During it `[A: /core-concepts]`:
- Nodes may carry **less weight**, since many contracts are set to expire
- Positions roll off or reset → **temporary distortions** in dealer positioning
- *"After OPEX week passes, **directional bias** and **map clarity** typically **improve immediately**"*
- *"Treat OPEX week levels with caution — probabilities are temporarily skewed"*

### 5.9 Deflection margin — think in zones

| Instrument | Documented margin | Source |
| --- | --- | --- |
| SPX | **~5–10 points** deflection margin around King Nodes | `[A: /core-concepts]` |
| SPX | Allow **5–10 points of margin** around high-value nodes | `[A: /common-pitfalls-and-mistakes]` |
| SPX | **±5 points** ("Plus/Minus 5 Rule") | `[C: Heatseeker 101, SPX_3000_Percent_Crash_Case_Study]` |
| SPY / QQQ | **±$0.50** | `[C: SPX_3000_Percent_Crash_Case_Study]` |

Tier A is emphatic: *"King Nodes don't always reject **to the exact cent**… A rejection occurring 5½ points away from the King Node is still considered valid."* `[A: /core-concepts]` And: *"Don't demand **cent-perfect reversals**… Think **zones**, not single lines."* `[A: /common-pitfalls-and-mistakes]`

Also Tier A, for precision expectations: *"Price can often reject **within a few points** of the King Node — precision to the cent is not required."* `[A: /how-to-read-and-use-heatseeker]`

### 5.10 Robinhood Power Hour

*"At **3:30 PM EST** (30 minutes before market close), Robinhood begins **auto-liquidating** accounts that fail margin requirements. This creates **forced order flow** in highly liquid names like **SPX, SPY, and QQQ**."* `[A: /core-concepts]`

Why it matters `[A: /core-concepts]`:
- Triggers **volatility spikes** right before close
- Can **force breakouts or fakeouts** near Gatekeeper Nodes
- Near a **King Node**, can **accelerate** the move
- *"Sometimes reshuffles the entire map"*
- *"Always stay alert to **Power Hour volatility** — it's often broker-driven and mechanical, not organic"*

Tier C frames the same window as *"3:30 PM – 4:00 PM… Robinhood auto-liquidations and dealer hedge flattening."* `[C: SPX_0DTE_Sniping_Masterclass]`

---

## §6. Dealer mechanics — why nodes work

Source for this whole section: ***The Greeks Bible — Skylit Edition*** (Tier B), the platform's own theory text. It opens with an explicit **educational-use-only disclaimer** — see §18.

### 6.1 The Three Golden Rules

> 1. **GEX defines potential; VIX defines reality.**
> 2. **0–5 DTE = Gamma world. 7–180 DTE = Vanna world.**
> 3. **Trade where Greeks and the vol regime agree.**

`[B: Greeks Bible p.2]`

Restated later as the "Table Tilt" framework: *"GEX shows where the tilt **could** occur; VIX decides **if** it will."* `[B: Greeks Bible p.29]`

### 6.2 The Casino frame

| Role | Identity | Behavior |
| --- | --- | --- |
| **Dealer (Market Maker)** | **The Casino** | *"Hedged, earns from flow, manages risk through Greeks."* Must react **mechanically** to crowd exposure. |
| **Player (Customer/Fund)** | **The Gambler** | *"Directional, expresses bias through options, creating dealer hedging flows."* Runs out of gamma/delta leverage once ITM. |

*"Each Greek is a dimension of risk the dealer must neutralize. When crowd exposure shifts, dealers rebalance — their hedges feed back into price, volatility, and liquidity."* `[B: Greeks Bible p.2]`

The thesis in one line: *"When you trade Heatseeker, you're not betting on **direction** — you're betting on **who will be forced to hedge next**."* `[B: Greeks Bible p.14]`

Base hedging identity: *"If dealer is **long delta** → they **sell stock** to neutralize; **short delta** → they **buy stock**."* `[B: Greeks Bible p.5]`

### 6.3 GEX: the sign determines the reflex

| Condition | Dealer hedge | Market behavior |
| --- | --- | --- |
| **+GEX** (dealers long gamma) | **Buys dips, sells rips** | Stability, mean reversion. *"Contrarian"* → stabilizing |
| **–GEX** (dealers short gamma) | **Sells dips, buys rips** | Volatility amplification. *"Pro-cyclical"* → amplifying |

`[B: Greeks Bible p.5–6]`

Positional reading: *"+GEX below = support, +GEX above = resistance."* And: *"SPX 0DTE fades in +GEX; trends in –GEX."* `[B: Greeks Bible p.6]`

Vol interaction: *"Vol ↑ amplifies –GEX; Vol ↓ strengthens +GEX pins."* `[B: Greeks Bible p.6]`

Tier C convergent framing — negative gamma is **pro-cyclical**: *"buying leads to more dealer buying, and selling leads to more dealer selling. Breaking below them triggers a 'sell loop'."* `[C: SPX_3000_Percent_Crash_Case_Study]`

### 6.4 VEX: the sign plus position determines the vol reflex

| Exposure | Vol ↓ | Vol ↑ | Market bias |
| --- | --- | --- | --- |
| **+VEX below spot** | Buys stock | Sells stock | Supportive in calm → **pin & drift** |
| **–VEX below spot** | Sells stock | Buys stock | Fragile floor → **whipsaw** |
| **+VEX above spot** | Buys stock | Sells stock | Resistance melts → **breakout in calm** |
| **–VEX above spot** | Sells stock | Buys stock | Elastic ceiling → **squeeze in stress** |

`[B: Greeks Bible p.8]`

Summarized: **+VEX** → *"supportive when vol is falling, suppressive when vol is rising."* **–VEX** → *"destabilizing when vol compresses, cushioning when vol explodes."* `[B: Greeks Bible p.10]`

### 6.5 Spot *between* nodes — the tug-of-war zone

This is the most common state an agent will encounter, and the Greeks Bible flags it as the hardest: *"When spot trades **between two large nodes** (e.g., a +GEX shelf below and a –GEX pocket above, or mixed VEX clusters), price action becomes a contest of opposing dealer hedges. This is **the most confusing zone** for new Heatseeker users — because it's where **flow polarity flips back and forth intraday**."* `[B: Greeks Bible p.14]`

| Configuration | Dealer positioning | Vol behavior | Market character |
| --- | --- | --- | --- |
| **+GEX below, –GEX above** | Buy on dips (below) and chase higher (above) | Stable vol → calm oscillation; vol ↑ → air-pocket breaks | **Compression that can rug or sling fast** |
| **–GEX below, +GEX above** | Sell on dips and fade rallies | Vol ↓ → repeated fakeouts; vol ↑ → large breaks | **Choppy, deceptive range** |
| **Between +VEX and –VEX clusters** | Dealers flip from buying to selling as vol shifts | Small vol shifts swing control | **IV spikes = directional chaos** |
| **Trapped between two King Nodes** | Massive opposing hedges at adjacent strikes | Vol flat → *"sticky magnetism"* | **Ranges shrink intraday** |

*"When spot sits between two magnets of opposite charge, it **vibrates — not trends**. It only escapes when one magnet weakens (IV shift or OI decay)."* `[B: Greeks Bible p.15]`

**Documented tactics for this zone** — note the explicit no-chase stance `[B: Greeks Bible p.35]`:
- **Edge probes:** fade the **first touch** of either edge, with a tight stop just beyond the node
- **Failed push = signal:** if price pierces an edge and **snaps back** as VIX curls the opposite way, re-enter **with** the move — *"it's the losing side tapping out"*
- **Vol decides:** in chop, wait for **VIX direction** to pick the winner, then ride pullbacks toward the winning side's node

**Agent consequence:** when `spot` sits between two comparable-`abs(value)` nodes, do **not** report a directional thesis. Report a tug-of-war, identify which configuration applies, and name what would break the tie (a vol shift or OI decay).

### 6.6 Combined GEX × VEX scenario matrix

| Setup | Dealer flow | Market behavior |
| --- | --- | --- |
| **+GEX +VEX below spot** | Buy dips (gamma) + buy as vol falls (vanna) | **Tight range, upward drift ("melt-up")** |
| **+GEX +VEX above spot** | Sell rips + sell more as vol rises | **Ceiling thickens; repeated rejections** |
| **–GEX –VEX below spot** | Sell dips + sell as vol falls; both pro-cyclical | **Sharp breakdowns, low-liquidity slides** |
| **–GEX –VEX above spot** | Buy rips + buy more as vol rises; reflexive loop | **Violent short squeezes, vol spikes** |
| **+GEX below / –GEX above** | Buy low, chase high — unstable transition | **Rug-pull if vol ↑, slingshot if vol ↓** |
| **–GEX below / +GEX above** | Sell low, fade high — compression zone | **Chop until breakout** |

`[B: Greeks Bible p.11–12]`

### 6.7 Strike lifecycle and hedge exhaustion — the reversal engine

This is the mechanism behind "why price reverses at big nodes". Learn it; it is the most useful explanatory tool in the corpus.

| Phase | Option status | Delta | Dealer hedge | Market impact |
| --- | --- | --- | --- | --- |
| **1. Far OTM** | Speculative chips | Δ ≈ 0 | Dealer does almost nothing | **Price ignores the node** |
| **2. ATM** | Maximum sensitivity | Δ ≈ 0.5 | Must adjust hedge rapidly | **Volatility explodes; feedback loop active** |
| **3. Deep ITM** | Deltas saturated | Δ → ±1 | Hedge fully sized; no more adjustment | **Flow stops; feedback collapses** |

*"This means the **danger zone is the approach** — not once we're 'through it.'"* `[B: Greeks Bible p.20]`

**Walkthrough of a –GEX node below spot** `[B: Greeks Bible p.21]`:
- **Gambler:** bought puts earlier. As spot drops toward strike, puts go OTM → ATM → ITM; delta rises, profit explodes. Once ITM, payoff flattens; many **cash out or roll down**.
- **Dealer:** sold those puts → long delta as price falls → **sells stock** to stay neutral, which **accelerates the drop**. Once puts are deep ITM, delta ≈ –1 and **stops changing** → dealer **stops selling**. When gamblers close, dealers **buy back hedges** → reversal.

Four documented reversal drivers `[B: Greeks Bible p.21–22]`:
1. **Dealer hedge exhaustion** — deltas saturate, *"selling pressure suddenly disappears, leaving vacuum liquidity"*
2. **Gambler profit-taking** — buyers sell to close → dealers buy back stock
3. **Charm & theta** — decaying OTM options lose delta → dealers buy stock back → *"passive upward drift"*
4. **Vanna support** — if IV falls after the move, +VEX below spot activates → dealers buy to re-hedge

Result: *"A cascading **sell → stop → buy** sequence — creating what looks like a 'mysterious' bottom."*

**The two analogies** — high-utility for explaining to users `[B: Greeks Bible p.21, p.27]`:
> *"Think of a beach ball underwater. As you push it deeper (toward a negative GEX pit), the pressure builds (dealer selling). But once it hits bottom (ITM saturation), the pressure releases — and the ball pops upward."*

> *"A –GEX pit is like sand collapsing under your feet — the faster you fall, the more the sand gives way, until it hardens at the bottom and slings you upward."*

**Why +GEX/+VEX appears *after* a reversal** — structural, not coincidence `[B: Greeks Bible p.22–23]`: puts closed → calls opened → dealers flip from short to slightly long delta → **new +GEX cluster forms** → vol crush confirms → range re-establishes. *"After a storm, lifeguards (dealers) rebuild the sandbar closer to shore. That new sandbar becomes another magnet node."*

Tier C's independent arrival at the same mechanism, useful phrasing: *"As puts go In-The-Money, gamblers cash out, forcing dealers to buy back their short hedges, creating a 'trampoline effect'."* `[C: SPX_0DTE_Sniping_Masterclass]`

### 6.8 Why some huge distant nodes do nothing

*"A huge node far from spot isn't 'wrong' — it's just **inactive for now**."* `[B: Greeks Bible p.28]` And critically: *"Now this doesn't mean it will never hit (it might!)… just that the pull won't be as strong unless/until price starts to approach it."*

| Reason | Explanation | Analogy |
| --- | --- | --- |
| Deltas too small | Far OTM → Δ near 0 → no hedge flow | *"A huge magnet far away — can't feel it yet"* |
| Wrong vol regime | IV too low → VEX dormant; or IV too high → dealers already hedged | *"The wind's blowing the wrong way"* |
| Different expiry | Node lives on a later maturity — different players, different book | *"Different poker table"* |
| OI already rolled | Traders exited → ghost exposure | *"Empty chairs at the table"* |

`[B: Greeks Bible p.28]`

**Three-state classification** an agent can apply directly `[B: Greeks Bible p.34]`:
- **Dormant** — no near-term Δ sensitivity
- **Warming** — spot drifts closer, or the IV path starts to engage vanna
- **Active** — Δ sensitivity + IV path + tenor alignment → *"flows 'turn on'"*

*"Exposure doesn't matter as much until delta sensitivity (Γ × OI × proximity) gets large enough to generate flow."* `[B: Greeks Bible p.28]` — and *"Distance = delta sensitivity, not just notional size."* `[B: Greeks Bible p.24]`

### 6.9 Stacked nodes — the volatility spring

*"When you see **stacked exposures of opposite sign**… think of it as a **spring under tension**. Crossing that stack triggers sudden shifts in dealer hedging direction."* `[B: Greeks Bible p.15]`

| Stack (upper over lower) | Dealer transition | Market reaction | Documented tactic |
| --- | --- | --- | --- |
| **+GEX over –GEX** | Buying dips → selling dips | **"Rug pull"** down through the flip | Short the **first failed retest** from below; stop back above stack |
| **–GEX over +GEX** | Selling dips → buying dips | **"Slingshot"** up through the flip | Long the breakout through stack; stop below |
| **+VEX over –VEX** | Selling in stress → buying in stress | Relief rally through resistance | Long if IV contracting |
| **–VEX over +VEX** | Buying in stress → selling in stress | Sharp rejection or whipsaw | Fade strength if vol spiking |

`[B: Greeks Bible p.15, p.35]`

*"Stacks are like loaded springs — when price pushes through, dealer hedges flip polarity and release stored energy."* `[B: Greeks Bible p.16]`

Note also *"**Ladder of Purples:** multiple small –GEX islands can cascade once one flips; each acceptance fuels the next."* `[B: Greeks Bible p.32]`

**Cross-reference:** the `+GEX over –GEX` row is the exact structure Patternpedia calls **RUG SETUP** (§9.1).

### 6.10 GEX/VEX alignment vs misalignment

| State | What it means | Market behavior |
| --- | --- | --- |
| **Aligned +GEX +VEX** | Dealers long gamma **and** long vanna | Market stable, supportive drift |
| **Aligned –GEX –VEX** | Dealers short gamma **and** short vanna | Pro-cyclical acceleration, high vol |
| **Misaligned +GEX –VEX** | Long gamma but short vanna | Conflicting hedges → **range + sudden breaks** |
| **Misaligned –GEX +VEX** | Short gamma but long vanna | Short-term volatility, longer-term support |

`[B: Greeks Bible p.16]`

**Four documented causes of misalignment** — cite these when a map looks self-contradictory `[B: Greeks Bible p.17]`:
1. **Time skew** — 0DTE & 1DTE dominate **GEX**; 5–30DTE dominate **VEX**; short- vs mid-term flows diverge
2. **Skew asymmetry** — puts have much higher vanna, so downside +VEX below spot can persist even when GEX flips negative
3. **OI rotation** — as expiries roll off, GEX collapses while VEX persists (longer tenor)
4. **Vol-regime change** — a sudden IV spike (news, CPI, FOMC) alters vanna sign faster than GEX updates; *"dealers instantly change vol hedges, but OI-based GEX still shows prior positioning → apparent mismatch"*

*"When GEX and VEX disagree, the dealer's two hands are pulling in opposite directions. Whichever Greek dominates (Gamma intraday, Vanna across days) decides the winner."* `[B: Greeks Bible p.18]`

**Reading Heatseeker for alignment context** — the operational lookup table, including two regimes the scenario matrix in §6.6 does not cover `[B: Greeks Bible p.18]`:

| Observation | Interpretation | Expected flow | Trade implication |
| --- | --- | --- | --- |
| +GEX and +VEX clusters in same region | Dealer control, long vol compression | Pin & drift | Fade extremes; buy-the-dip bias |
| –GEX but +VEX stacking below spot | Gamma short, vanna supportive | Vol-crush rebound likely | Long into panic lows |
| +GEX but –VEX above spot | Gamma pins but vanna suppresses | Ceiling holds until vol collapses | Short rips in calm; flip long if vol crushes |
| **GEX flat, VEX dominant** | **Low delta convexity, vol-driven market** | **Price follows VIX flow** | **Trade the vol regime, not direction** |
| **GEX/VEX polarity shift at the same strike** | **Flip zone confirmed** | **Directional acceleration** | **Use as a breakout trigger** |

Two agent-relevant consequences of the last two rows:

- **A "flat" GEX map is not automatically a no-read.** If gamma shows no strong structure but the vanna map does, the signal has moved to VEX and the market is vol-driven. Do not label this **Rainbow Road** (§9.1) without checking `metric=vanna` first.
- **A same-strike polarity flip in *both* metrics is the strongest confirmation available from two calls.** When running the §16.3 workflow, explicitly test for a strike where `sign(value)` flips between the gamma and vanna maps — the Greeks Bible rates this a confirmed flip zone and a breakout trigger, above any single-metric flip.

### 6.11 Cross-expiry hierarchy — "The Hidden Hands"

| Tenor | Governs | Greek |
| --- | --- | --- |
| **0–2 DTE** (front) | **Speed** | Gamma hedging |
| **7–30 DTE** (middle) | **Drift** | Vanna hedging |
| **45–90 DTE+** (back) | **Gravity** | Vol regime |

`[B: Greeks Bible p.19]`

*"When front-end GEX and mid-end VEX disagree, you get intraday whipsaws inside multi-day trends — the classic 'grind higher but chop intraday' behavior."* `[B: Greeks Bible p.19]`

The override that matters most `[B: Greeks Bible p.8, p.13]`:
- **Vol spike:** *"When front-end +GEX looks supportive, far-expiry +VEX can **flip it bearish** — dealers suddenly selling stock to re-hedge long vol exposure."*
- **Vol crush:** *"Far-dated +VEX reinforces +GEX, producing **multi-day pin and melt-up**."*

Tier C states this as a headline methodology update: **"VEX overrides GEX"** during volatility spikes / geopolitical news. `[C: SPX_3000_Percent_Crash_Case_Study, SPX_0DTE_Sniping_Masterclass]`

### 6.12 Vanna & charm drift — why markets float

- **Vanna drift:** IV compression → dealers buy back hedges (from +VEX) → price drifts up
- **Charm drift:** time decay reduces deltas → dealers rebalance by buying → **afternoon upward drift**
- *"Charm and Vanna combine in calm sessions to produce steady upward 'melt.'"*

`[B: Greeks Bible p.8–9]`

Tier C observes the same: *"Mid-day (post-lunch) often sees a natural drift upward if volatility is stable or compressing. This is due to dealers buying back hedges as Vanna and Charm decay."* `[C: SPX_0DTE_Sniping_Masterclass]`

### 6.13 The mental models — use these verbatim when explaining

> **GEX = brakes. VEX = slope. VIX = weather.**
> **Loops die at saturation** (Δ stops changing) → that's your reversal fuel.
> **Distance to exhaustion = your edge.**

`[B: Greeks Bible p.38]`

Also `[B: Greeks Bible p.13, p.14, p.30]`:
- *"Gamma is the brake pedal, Vanna is the slope of the hill. You can tap the brakes intraday, but if the hill is steep (vol regime), you still roll in that direction."*
- *"Dealer = Casino. Player = Crowd. GEX = Table Shape. VEX = Gravity. Vol = Energy."*
- *"The market is a lake. Gamma are the waves, Vanna is the wind, Charm is the tide, Theta is the sun drying the water."*

### 6.14 Practical signal map (Tier B tactical table)

| Signal | Interpretation | Tactical bias |
| --- | --- | --- |
| +GEX and +VEX stack under spot | Dealer net long gamma + long vanna = full control | Fade dips, expect compression |
| –GEX and –VEX stack under spot | Dealer short gamma + short vanna = panic zone | Trend following; avoid fading |
| Crossing a flip zone (+ over –) | Polarity change in dealer flow | Expect rug-pull or slingshot |
| Large King Node | Max exposure → reversion magnet | **Trade around, not through it** |
| Wide air pocket | Sparse exposure → fast price zone | Expect acceleration |
| Vol crush + +VEX | Supportive → rallies sustain | Ride melt-up |
| Vol spike + +VEX | Dealer selling → resistance zone | Fade rally / reduce longs |
| Vol spike + –VEX | Dealer buying → squeeze risk | Ride upside but expect whipsaws |

`[B: Greeks Bible p.13]`

### 6.15 The breakout paradox, resolved

Worth quoting because it resolves an apparent contradiction users will hit `[B: Greeks Bible p.12]`:

> *"If dealers sell when vol ↑, why buy the breakout? Because **the dealer flow is reactive, not predictive**. When you see '+VEX above spot,' it means dealers will sell into rising vol — but the first leg of that vol expansion **is the breakout itself**. The breakout **creates** the vol spike. Dealers' **selling comes slightly after**, providing resistance. If that selling flow fails to cap price (absorbed by real demand), you get a vol-crush continuation."*
>
> *"✅ So you buy breakouts only in **vol compression**, where +VEX resistance is **melting**, not strengthening. ❌ You don't buy breakouts if vol is expanding — that's when +VEX resistance **hardens**."*

---

## §7. The official 5-step reading framework

This is the canonical procedure. `[A: /how-to-read-and-use-heatseeker]`

| Step | Focus | Goal |
| --- | --- | --- |
| **1. Identify the Magnets** | Major nodes | Prepare deflection levels |
| **2. Spot the King Node** | Dealer settlement target | Set end-of-session bias |
| **3. Define the Range** | Range extremes | Fade edges, avoid midpoint |
| **4. Watch Gatekeeper Nodes** | Rejection zones | Detect early reversals |
| **5. Map the Flow** | Dealer shifts | Adapt to reshuffles |

**Step 1 — Identify the Magnets.** Larger node values = stronger magnets. The closer price drifts, the stronger the pull. Mark horizontal lines at key nodes. *"Focus only on the **most significant nodes** — cluttering your chart with too many can obscure clarity."*

**Step 2 — Spot the King Node.** Treat it as the **primary destination target** for the session; mark it as the EOD/EOW anchor; watch for early-day **drive-offs** and late-day **pinning**.

**Step 3 — Define the Range.** *"Ranges provide some of the **highest-probability setups** and **best risk-to-reward ratios**."* Identify where price consistently reverses; at the extremes, **fade the edge** rather than chase momentum; avoid midpoints.

**Step 4 — Watch Gatekeeper Nodes.** Expect **map reshuffling** or directional change on rejection. Early-day rejections often mark powerful reversals. *"Align your read across **SPX, SPY, and QQQ**… **Rule of thumb:** SPX, SPY, and QQQ must **agree** — if one diverges, stand aside."*

**Step 5 — Map the Flow.** Watch for:
- **Accumulation** — dealers building positions → stronger magnet zones
- **Dissipation** — dealers closing positions → weakening nodes
- **Reshuffling** — rapid exposure changes → new structure forming

*"By playing **direct deflections** off key nodes, you're essentially trading **ahead of the reshuffle**, not after it."*

### 7.1 The Skylit-published agent workflow

`mintlify://skills/skylit` publishes an explicit 7-step agent procedure — treat this as the reference implementation of §7 `[A: mintlify://skills/skylit]`:

1. Fetch the live heatmap for your symbol(s)
2. **Identify the King Node** — highest absolute-value strike
3. **Spot Gatekeeper Nodes** — strong nodes blocking the path to the King
4. **Define the range** — upper/lower boundaries; avoid midpoints, fade edges
5. **Watch for reshuffles** — if the map changes significantly, pause and re-evaluate
6. **Combine with price action** — *"Heatseeker is context, not a signal"*
7. **Check confluence** — SPY/SPX/QQQ must agree; divergence = lower probability

### 7.2 Best practices (Tier A, six rules)

`[A: /best-practices]`

1. **Use nodes as context, not signals.** *"Think of Heatseeker™ as your **map** — price action is the **compass**."* A node's significance depends on how price reacts to it, not just its size.
2. **Stay fluid, not biased.** *"**Discipline > conviction.** You're reading flow, not fighting it."*
3. **Focus on asymmetric risk-to-reward.** *"If risk and reward are close to 1:1, skip the trade — there's no edge… Winning traders think in *ratios*, not in *win rates*."*
4. **Don't fight the map.** *"When in doubt, zoom out. A new map = new game."*
5. **Combine GEX and VEX for confirmation.** Overlap zones carry stronger influence. *"**Confluence = Confidence.**"*
6. **Look for confluence among the indices.** *"Ask Yourself: 'If I were to take a trade based off SPX heatmap, would my thesis still hold true if I took it on QQQ instead?'"*

The worked example under rule 1 is instructive and quotable: *"LLY for the week of October 6th, 2025 showed a king node at $900, but had already had a massive leg up. The probability that this 900 node will get hit is much less because of the massive upside rally that had already occurred."*

### 7.3 The Ten Commandments

Tier B, Patternpedia. Reproduced verbatim — these are risk-discipline rules, not tool mechanics. Present them as the source's guidance, never as your own recommendation (see §18). `[B: Patternpedia/the-ten-commandments-of-using-heatseeker]`

1. Thou shalt protect your existing capital at all costs.
2. Thou shalt only trade reversals at floors or ceilings (not diddle in the middle)
3. Thou shalt only trade asymmetric risk reward setups
4. Thou shalt always consider where price was delivered from
5. Thou shalt be aware of the broader market
6. Thou shalt put technical analysis before utilizing Heatseeker
7. Thou shalt seek confluence between Heatseeker and Price Action
8. Thou shalt not oversize
9. Thou shalt go long on red candles, and go short on green candles
10. Thou shalt not let a green trade go red.

Closing note from the source: *"Above all, practice sound risk management and protect your profits. Recall these commandments regularly, even in the wake of a green day. The market will always find a way to humble you."*

> **Commandment 6 is the load-bearing one for an agent.** *"Put technical analysis before utilizing Heatseeker."* The official pitfalls page states the same hierarchy formally: **1. Price Action → 2. Heatseeker Confluence → 3. Asymmetric R:R.** `[A: /common-pitfalls-and-mistakes]` Never present a Heatseeker read as sufficient on its own.

### 7.4 Trade-idea validation (Tier A, three checks)

Before entering, confirm three points `[A: /faqs]`:
1. **Node Context** — identify nearby magnets (King or Gatekeeper)
2. **Price Reaction** — wait for confirmation or rejection wick
3. **Range Alignment** — ensure SPX, SPY, and QQQ are in agreement

*"When all three align — that's conviction."*

---

## §8. Node dynamics over time

Static reads are weak reads. Everything in this section requires either `velocityPct` or ≥2 snapshots.

### 8.1 Rate of change / velocity

*"Heatseeker™ not only shows **dealer positioning**, but also **how fast liquidity changes**."* `[A: /core-concepts]`

| Reading | Meaning |
| --- | --- |
| **Rapid accumulation** | *"Dealers are quickly adding exposure; acts like a **magnet** that pulls price in strongly"* |
| **Rapid unwinding** | *"Exposure vanishes; levels that looked strong may suddenly **weaken**"* |
| Either, fast | *"Fast changes often cause **volatility spikes**, **explosive moves**, or **sharp reversals**"* |

*"Watch the **rate of change** — it reveals urgency and intent behind Market Maker adjustments."* `[A: /core-concepts]`

**API mapping:** `velocityPct` = *"Live percent change of this strike's value over the velocity window."* Live only. The window duration is **not documented** — never state one. `[A: /openapi/openapi.yaml]`

The docs' own worked example (image caption, `/core-concepts`) is the cleanest illustration in the corpus: *"SPY 660 bounce from October 13th, 2025. QQQ had a strong floor that wasn't supporting lower, SPX was showing upside accumulation. Due to the fact that QQQ did not support lower and SPX began growing to the upside, we had 2/3 confluence for longs. The rapid unwinding of SPY downside nodes led to a sharp, V-shaped recovery, emphasizing the importance of watching nodes for rate of change."* `[A: /core-concepts]`

Tier C thresholds — **heuristics, not spec**: a node growing ~50% (e.g. 10M→15M, or 30M→45M) while price is stagnant marks it as becoming the dominant magnet `[C: Heatseeker 101, Heatseeker_Recap_12-13-25]`; a *"10-minute rate of increase"* of ~20% preceded a major move in one case study `[C: SPX_3000_Percent_Crash_Case_Study]`; one session cites a node going 25M→150M in a morning `[C: Session 3]`.

### 8.2 Accumulation · dissipation · reshuffle

The three flow states from Step 5 `[A: /how-to-read-and-use-heatseeker]`. A **reshuffle** is the one an agent must handle deliberately:

*"A **map reshuffle** is not noise, it's the market **changing its structure**. When a reshuffle occurs, **pause** and reassess. Don't assume previous nodes still matter. Many traders lose money trying to trade *yesterday's map in today's market*."* `[A: /common-pitfalls-and-mistakes]`

*"A reshuffle signals that dealers have **adjusted risk**. Your job is to **observe**, not fight the change."* `[A: /common-pitfalls-and-mistakes]`

On recovery `[A: /faqs]`: take a step back and reassess structure → mark new King and Gatekeeper Nodes → *"Avoid trading until the new structure stabilizes."* Reshuffles *"often follow **large volatility events** — like CPI, FOMC, or Power Hour."*

Gatekeeper linkage: *"If price **tests and fails** at a Gatekeeper Node → the map can **reshuffle**. **Reshuffles** often precede a **trend change** or **realignment** of where dealers aim to pin price."* `[A: /core-concepts]`

**Detection procedure for an agent** — the API has no `reshuffled` flag:
1. Call `heat_heatmap`, persist `asOf` + the ranked node list.
2. Re-call after ≥60s (§2.4).
3. Compare: did the `king` strike move? Did any `gatekeeper` appear/vanish? Did the top-3 by `abs(value)` reorder? Did large `velocityPct` magnitudes appear?
4. If yes → report a structural change, restate the new King/Gatekeeper set, and recommend re-assessment rather than carrying the old read forward.

Tier C adds frequency and cause: dealers now adjust hedges continuously rather than weekly, so *"heat maps will shuffle multiple times per day (0–3+ shuffles)."* `[C: Session 5]` And a direction-of-effect claim: reshuffles tend to **help** a position entered at a deflection level and **hurt** one entered chasing a distant node. `[C: Session 10]`

### 8.3 Rolling of ceilings / floors

Tier A, and one of the most actionable derived signals `[A: /core-concepts]`:

| Behavior | Definition | Read |
| --- | --- | --- |
| **Rolling of ceilings** | Upside ceiling and/or upside price targets **decrease in value**, with the ceiling **moving to a lower strike** | *"Strong presumptive evidence of a **bearish** thesis playing out"* |
| **Rolling of floors** | Downside floor and/or downside targets **decrease in value**, with the floor **moving to a higher strike** | *"Strong presumptive evidence of a **bullish** thesis playing out"* |

Requires two snapshots: compare both the **strike location** and the **value** of the extreme node on each side.

Tier C corroborates from the opposite direction — *"the put floor actually rolled up from 5900 to 5935–5940 (bullish signal — floor rising)."* `[C: Session 0]`

### 8.4 The divergence signal

Tier C, but internally consistent across three independent files, and it is the highest-value derived signal in the corpus. **Attribute it as bootcamp material.**

**Definition:** price moves one way while node values move the other. `[C: Session 0]`
- Price drops, but the **downside node shrinks** while the **upside node grows** → bullish divergence
- Price rises, but the **King Node stays fixed** while a **lower floor doubles** → expect retrace `[C: Session 7]`

Session 0's worked example: SPX dipped below 5930; the 5900 downside node fell (~5,150 → ~4,000-range) while 5960 nearly doubled (~5,100 → ~9,124). Read as dealers positioning for upward movement; described as *"a 'major divergence'"* and *"an 'A-plus map' — rare but unmistakable."* `[C: Session 0]`

The mirrored bearish version, and the reason it matters for exits: *"If price breaks above a resistance node (e.g. 6500) but the node's negative gamma increases (e.g. -75M to -83M), the breakout is fake. The magnet is pulling it back down."* `[C: Heatseeker 101]`

Post-bounce version: *"After a bounce, the floor node should shrink (35M → 25M). If price bounces but the floor node **grows** (35M → 60M), this is a **Bearish Divergence**. The bounce is fake."* `[C: Heatseeker 106]`

**Agent implementation:** requires `velocityPct` (live) or two `/v1/historical` calls. Compare `sign(price change)` against `sign(velocityPct)` at the relevant floor and ceiling. Report as a *divergence between price direction and dealer accumulation*, and label the tier.

### 8.5 Price delivery and retest decay — Tier A

*"When looking for **bounce plays** off a node, **context matters** — not all nodes retain influence forever."* `[A: /core-concepts]`

| Touch | Reaction strength | Probability | Notes |
| --- | --- | --- | --- |
| **First** | Strongest | Highest | Fresh liquidity; MMs defend aggressively |
| **Second** | Moderate | **~66%** | Often forms double tops/bottoms |
| **Third+** | Weakest | **~33%** | Reduced probability of reversal |

*"If price has already been **delivered from** a node (touched and moved away), its **influence weakens**. Each additional test reduces the likelihood of a strong bounce."*

***"Key Takeaway: Prioritize **untouched nodes** for bounce plays — treat previously tested ones with caution."***

And separately `[A: /core-concepts]`: *"A node that has been interacted with tends to have less influence over price action, because the node is no longer 'fresh'… **Instead of trying to chase a node that has already been interacted with, it is better to move on to the next trade. Looking for the freshest levels possible provides the highest probability for success.**"*

**The resolution rule** — how to tell whether price returns to the node it was delivered from `[A: /core-concepts]`:

| Observation after delivery | Read |
| --- | --- |
| **Gradual decrease** of the node price was delivered from | **Low** probability of a return to that node |
| **Increase** of the node price was delivered from | **Higher** probability of reversion back to it |

*"By keeping an eye on key nodes for rate of change, we can make inferences as to whether a reversion back to a deflection node is likely."*

> ⚠️ **Tier C disagrees with the 66%/33% table.** See §15 — this is the single most contradicted number in the corpus. Quote the Tier A table; name the conflict if the user relies on it.

### 8.6 Node death and resuscitation

Tier C, consistent across two files. It reconciles apparent contradictions in the retest data, so it is worth carrying:

*"Levels that appear to 'diminish' on the heatmap as price moves away are often just experiencing **delta decay**, not necessarily position closing. They can **'come back to life'** and become magnetic or resistant again if price returns to them."* `[C: SPX_0DTE_Sniping_Masterclass]`

*"If price moves away from a node, the exposure numbers decrease (delta decay). However, the position is still there. If price returns to that level, the node 'comes back to life' aggressively as dealer hedging obligations re-expand."* `[C: SPX_3000_Percent_Crash_Case_Study]`

This is mechanically consistent with Tier B §6.7 (Δ→0 far OTM = dormant, not gone) and §6.8 (dormant → warming → active). Safe to explain using the Tier B mechanism and cite Tier C for the label.

**Contrast with "wash-off"** — Tier C's opposite claim: once a target node is tapped, the exposure *"washes off"* and *"Dealers have moved on"* — do not re-trade a level that already paid out (the **"Sloppy Seconds" rule**). `[C: Heatseeker 104]` These two Tier C ideas are in tension; both are recorded in §15.

---

## §9. Pattern library

### 9.1 Canonical Patternpedia patterns (Tier B)

Six named patterns. These are the official vocabulary — prefer these names.

---

#### PATTERN: The Whipsaw
`[B: Patternpedia/pattern-the-whipsaw]`

**Description:** *"Price trades in a wide range, the edges of which are defined by the presence of at least two high value nodes with few prominent nodes in between."*

**How to approach:** *"Similar to trading a range — the highest risk/reward opportunities lie in fading the edges of the range. It is strongly advisable to avoid initiating a position in the middle of the range, especially with 0DTE options, as theta decay and the unstable behavior of price can rapidly erode premiums."*

**Published examples:** SPX oscillating 6650↔6675 (09/24/25); loose range 6545↔6480 (09/09/25); oscillating across 6480 / 6460 / 6440 — *"note that in this example, there are four high value nodes of interest"* (09/05/25).

**Detection:** two high-`abs(value)` nodes bracketing spot, with only `normal`/small nodes between.

---

#### PATTERN: Rainbow Road
`[B: Patternpedia/pattern-rainbow-road]`

**Description:** *"Multiple prominent nodes of positive and negative values, in many cases bearing a resemblance to a rainbow. Another distinct feature of this heatmap is the **lack of a clear range** for price to trade within, heralding choppy and erratic price action."*

**How to approach:** *"It is highly advisable to **avoid trading** when positioning is vague. Instead, wait for a higher probability setup to form."*

**Published examples:** nodes spread across an 80-point range early in session (07/02/25); multiple prominent purple nodes in the 641–637 range (08/19/25); cluster across a 100-point range (08/07/25).

**Detection:** many nodes with comparable `abs(value)`, mixed signs, no dominant King, spread wide. **This is a "no-read" state — say so.**

---

#### PATTERN: The Gatekeeper
`[B: Patternpedia/pattern-the-gatekeeper]`

**Description:** *"A high-value node sits between price and further high-value nodes, preventing continuation. This node then serves as a key support or resistance level."*

**How to approach:** *"Mark out gatekeeper strikes on the chart, and scale out of a position as price approaches that level. One course of action would be to play a reversal as price approaches that node, **especially if the value of the Gatekeeper node is far in excess of the nodes beyond it**. As always, price action and market structure must dictate whether or not to take a trade off a gatekeeper node."*

**Published examples:** SPX 6600 (a key psychological level) gatekeeping price at 6615 from further downside nodes (10/10/25); CVNA supportive node at 360 between spot and the ultimate 350 target (10/08/25); SPY clear upside skew but a distinct gatekeeper at 664 stalling upside — *"compare the value of the gatekeeper node against the second highest-value node"* (09/29/25).

**Detection:** `nodeType == "gatekeeper"`, or a high-`abs(value)` node strictly between `spot` and the `king`.

**Sizing heuristic (Tier C):** *"A Gatekeeper is any node between Price and King Node that is at least **1/4 to 1/3 the size of the King Node**."* `[C: Heatseeker 105]`

**When a Gatekeeper is likely to *fail* (Tier C).** Neither Tier A nor Patternpedia gives a failure predictor. The closest thing in the corpus is a velocity override:

> *"A Gatekeeper is less likely to hold if the node below it is growing rapidly in velocity."* `[C: Heatseeker_Recap_12-13-25]`

The same source's reasoning: even if price bounces at a gatekeeper, rapid growth in the node beyond it means *"the magnet below is becoming stronger."* This is directly checkable — compare `velocityPct` at the gatekeeper against `velocityPct` at the next node past it. If the further node is growing faster, downgrade the gatekeeper. Label it as bootcamp material.

---

#### CASE STUDY: Speculative and Decoy Nodes
`[B: Patternpedia/case-study-speculative-and-decoy-nodes]`

**Description:** *"On occasion, a heatmap may display a far out of the money (OTM) node that is likely to be speculative or manipulative in nature. In both cases, traders should treat these heatmaps with a **high degree of skepticism**, and pair it with the chart to see if such a move is likely."*

**Example 1 — NFLX 1290.** A node at 1290, *"roughly 3 times the value of the 1232.5 gatekeeper node and a full 5.5% from where price was trading (1222)."* Three disqualifiers: (1) multiple gatekeeper nodes at 1232.5 and 1252.5; (2) *"a lack of prominent nodes to the upside for the next week over, reinforcing the impression that this node is more likely to be speculative or a decoy than a realistic price target"*; (3) *"price would have to run a full 5.5% within a span of 4 trading sessions… leaving almost no time for consolidation."*

**Example 2 — LLY.** On 10/6/25 the 10/10 900 strike showed the highest absolute value on the map, implying 50 points of upside. But *"price had already been delivered from the 710 strike in an almost vertical fashion, running almost 150 points in the past week with only two days of consolidation… Another 50 points of upside within a span of 4 days would be improbable at best."*

**Agent test — apply this before ever calling a distant node a "target":**
1. Is the node **far OTM** relative to spot? Compute % distance from `spot`.
2. Are there **gatekeeper nodes** between spot and it?
3. Is there **supporting accumulation** at nearby strikes, or is it isolated?
4. Has price **already run** hard in that direction (delivery context)?
5. Is the implied move **plausible in the sessions remaining**?

Failing 2+ of these → describe it as **possibly speculative or a decoy**, per Patternpedia. Cross-reference the Tier B "inactive node" mechanism in §6.8.

---

#### PATTERN: Trend
`[B: Patternpedia/pattern-trend]`

**Description:** *"Price fixates on a king node far away from spot with comparatively small counter-directional skew. As it trades towards the initial king node, it then selects the node above it as the new king node. If price begins to trade away from the king node, observe the changes in the values around that node's strike. If the values of those nodes remain relatively unchanged or even increase, it is a signal that price may eventually begin to trade towards it."*

**Mechanical signature:** *"the nodes away from price should **fade** in value while the values in the direction of the trend **increase** in value."*

**How to approach:** *"Because of the stair-stepping pattern of trend days, it is advisable to **enter on pullbacks** so long as price action and the heatmap support the trend. 'Chasing' price on a trend day should only be done to the extent that an asymmetrical risk/reward ratio allows."*

**Published examples:** price trading down to 6400 *"with few nodes of significance above spot to provide an opposing force"* (08/19/25); 6585/6580 providing an upward skew — *"important to actively manage positions as price approaches closer and closer to these dual strikes"* (09/11/25); price locking onto 6430 with *"a clear bullish skew as the absolute value of the 3 nodes above spot dwarf that of the 3 nodes below"* (10/01/25).

**Detection:** compute skew — sum `abs(value)` of the N nodes above spot vs the N below. The 10/01/25 example uses **N=3**. Large one-sided ratio + a distant King + weak counter-skew = Trend.

Tier C's version: *"a very unidirectional cluster of the major magnets with no opposing accumulation"* + the put floor rolling up. `[C: Session 0]`

---

#### PATTERN: RUG SETUP
by **John Wicks**. `[B: Patternpedia/pattern-rug-setup]`

**Description:** *"When we have a **yellow node stacked above a purple node** with no obvious floor in sight, we can identify the setup as a rug setup."*

**Why:** *"The negative gamma ceiling will accelerate the rejection of the positive gamma ceiling, almost like 'setting gasoline on the fire'. The negative gamma floors at 670 and 669 will also accelerate the move down."*

**Worked example — SPX/SPY/QQQ, 10/7/25:**
- Early in the day, *"heavy sized nodes for next day expiration, likely hedge nodes"*
- *"As the morning scramble began, I kept noticing SPX 6720 nodes growing gradually, with very little upside accumulation on all 3 indices. My bias at this point in time was a short bias."* → ***"no upside accumulating, downside growing"***
- **SPX:** very little activity at start of day, but growth to the downside
- **SPY:** downside pressure at 672/671 continued accumulating, preventing price from grinding higher; rug pull setup identified — *"when that yellow node unwinds, we can get rapid and violent reactions to the downside… All those negative gamma nodes below would also accelerate price nosediving, with no clear floor in sight."*
- **QQQ:** sitting above its king node with very little upside accumulation; 608/606 remained lit up → *"we can think of those nodes as price targets. All we needed was a catalyst, which in this case, was SPY's rug setup."*
- Actionable entries: rejection of 6750 on SPX, the double-top liquidity hunt on QQQ, or the bearish golden-pocket retracement on SPY

**Detection:** find adjacent strikes where the **upper** has `value > 0` and the **lower** has `value < 0`, then verify no significant positive node exists below. This is the same structure as Tier B's **"+GEX over –GEX → rug pull"** stack (§6.9).

Tier C corroborates independently `[C: SPX_3000_Percent_Crash_Case_Study, Heatseeker_Recap_12-13-25]`.

---

### 9.2 TOPPING / BOTTOMING PATTERNS — the VEX method
by **John Wicks & warren**. `[B: Patternpedia/topping-patterns-bottoming-patterns]`

*"This is a guide on how to spot potential higher timeframe trend shifts on the indices. We'll be looking primarily at the **VEX positioning** rather than the GEX positioning, as it is a reliable source for determining where dealers will be positioned in the sessions ahead."*

**Worked bearish case, week of 11/7/2025:**
- *"VEX was looking toppy on SPY and QQQ. We can determine this because of a **lack of accumulation to the upside nodes**."*
- *"685 SPY was a strong gatekeeper node with very minimal upside accumulation at 690. With price having been delivered from 690 a few days ago, I found the probability of us going back up to 690 was low."*
- *"QQQ had a similar outlook as SPY, with a king node at 635 and high accumulation into the next few days. Similar to SPY, we observed that same **stair-stepping pattern**. This is a significant confluence to consider."*

***"Key point: if the indexes are showing little to no upside accumulation, we can conclude that we may be reaching a 'market top'."***

**How to trade it:** *"by playing node rejections with the knowledge that dealers are postured in an extremely bearish manner. Keep in mind that maps can shuffle at a moment's notice, whether that's intraday or for the days ahead."*

**Invalidation** — quoted precisely, because a stated invalidation is rare and valuable: *"An invalidation of this bias would simply be the opposite of how it was formed: **higher accumulation at higher strikes, dissipation of values at lower strikes, and stronger positioning closer to the money**."*

**Outcome:** *"SPY sold all the way down to 661, with QQQ tapping its weekly low at 598, a nearly 3% move from peak to trough."*

**The page's own quiz** — the single best summary of the method's epistemology:
> *Q: What is a valid piece of criteria in identifying how market dealers are positioned?*
> **A: "Stairstep VEX exposure to the downside, with comparatively little accumulation to the upside."**
> (Rejected answers: an analyst report; $5m in SPY puts being bought; fib levels showing overextension.)

**Agent implementation:** call `heat_heatmap` with `metric=vanna`, then test for monotonic stair-stepping of `abs(value)` in one direction plus absent accumulation in the other. Note this is a **multi-day** read, not intraday.

### 9.3 Practitioner patterns from Tier C

**These are community coaching heuristics, not product documentation. Attribute every one.** They are included because they are internally consistent and name real structures — but an agent must not present them with the same confidence as §9.1.

| Name | Structure | Read | Source |
| --- | --- | --- | --- |
| **Slingshot** | Price dips into a major floor with a large gap to the next upside King and **no intermediate nodes** | Clean range → high-velocity move | `Heatseeker 102` |
| **Clear Skies** | Price hits a major floor and reverses, with **no upside nodes at all** | *"No upside nodes = No Resistance"* — removes the ceiling rather than removing the setup | `Heatseeker 106` |
| **Empty Basement** | SPY & QQQ at floors with **zero significant nodes underneath** | Floor treated as hard; a lower SPX magnet is discounted | `QQQ_SPY_Floor_Bounce_Recap` |
| **V-Shape / Liquidity Hunt** | Price breaks a major purple node but only undercuts it a few points, **and the node's value increases during the break** | Break is a fakeout; reversal back through the node | `Heatseeker 101` |
| **Breakout Trap** | Price breaks a resistance node **while that node's negative gamma grows** | Fake breakout — the magnet is pulling it back | `Heatseeker 101` |
| **Double Stacked Yellows** | Two large positive nodes stacked | *"Highly deflective"* — don't expect a first-test break | `Heatseeker_Recap_12-13-25` |
| **Node Pinching** | Two closely-spaced nodes whose **combined** value exceeds a wider spread of opposing nodes | Compressed cluster is the stronger side | `Session 0` |
| **Pivotal Node / equal-pull** | Two nodes of near-equal magnitude bracketing spot | Price oscillates; **midpoint is equilibrium**, not the larger node | `Session 5`, `Session 6` |
| **Node Cluster → zone** | 1 node = a line; 2–3 = a supply/demand **zone**; 3+ = extended range | Widen from level to zone | `Session 8` |
| **F-Map** | 5+ nodes lit simultaneously, conflicting, no clear structure | Untradeable. *"There's literally nothing to trade"* | `Session 8` |
| **Trampoline / Launch Pad** | EOD: next week's floor established + upside accumulation + negative gamma unwinding | Multi-day explosive-upside setup | `Session 7` |
| **Gap-Up Bull Trap** | A whole sector gaps up into its peak node on news/earnings | Read as institutions using gap-up liquidity to exit | `Session 8` |

**Note on overlap:** *F-Map* is the Tier C name for what Patternpedia calls **Rainbow Road** — prefer *Rainbow Road*. *Pivotal Node* restates Tier B's equal-pull magnet logic (§5.1). *Node Cluster → zone* restates Tier A's "Clusters of Nodes" priority tier (§3.3).

### 9.4 Official case studies (Tier A)

Three worked examples on `/examples-and-case-studies` — the best available demonstration of how Skylit itself reasons. `[A: /examples-and-case-studies]`

**TSLA puts off the king node, 10/2/2025.** *"Some of the best risk to reward trades come from taking direct deflection plays off gatekeeper nodes and king nodes."* Sequence: gapped up at open → hit the 470 king node → **no upside accumulation upon deflection** → downside began accumulating at 467, 462 and 450. On targets: *"By observing the rate of changes of nodes during a map reshuffle, we can make inferences as to where dealers are looking to reposition. In this case… we saw floors growing at 460 and 450."* The stated lesson: *"Given the fact that TSLA had an insane run up prior to October 2nd flush, our immediate thought process going into this morning should have been **not to chase pullbacks, but rather wait for a key pivot point** to hit and act as a catalyst for a reversal."*

**SPX puts off the 6750 ceiling, 10/9/2025.** Red flags, ranked by the source: **#1** *"major nodes to the downside very early on in the day… our first red flag for a rug occurred when those downside nodes popped up and grew significantly"*; **#2** SPY rejecting its 673 king node *"given that we were seeing floors beginning to grow at 670"*; QQQ telling the same story with the 611 gatekeeper tapped and rejected *"whilst seeing 607 and 605 grow."* Outcome: *"SPX hit 6715, SPY hit 670 and QQQ came within 50 cents of 607… Additionally, 670 SPY bounced to the cent. Calls off the deflection of the floor provided literally zero drawdown."*

**NFLX dip buy, 10/30/2025.** *"Wednesday at close, noticed that NFLX was approaching a strong floor at 1090, in line with a key level of support. Given the lack of downside nodes at that level, we had excellent RR on calls. **VEX showed a stair step up**, with good upside accumulation for the weeks to come. We enter at the direct test of 1090. As long as that level holds and we see upside growth, the play is valid."*

**What all three share** — this is the reusable template: (1) note delivery context / prior run; (2) identify the deflection node; (3) check for **accumulation on the far side**; (4) derive targets from **which nodes are growing**, not from the largest static node; (5) require cross-index agreement.

---

## §10. Confluence — how conviction is established

Confluence is the documented mechanism for turning a node into a thesis. Four independent layers.

### 10.1 Layer 1 — the Trinity (SPX · SPY · QQQ)

The strongest and most repeated rule in the entire corpus.

*"**Rule of thumb:** SPX, SPY, and QQQ must **agree** — if one diverges, stand aside."* `[A: /how-to-read-and-use-heatseeker]`

*"The best setups happen when **SPX**, **SPY**, and **QQQ** are aligned. **No confluence = no confidence.**"* `[A: /common-pitfalls-and-mistakes]`

The documented failure case, verbatim `[A: /common-pitfalls-and-mistakes]`:
> If **SPX** shows downside accumulation, **SPY** shows chop, and **QQQ** shows upside bias → *"Your setup's probability **drops drastically**."*

**Why the indices interact** — the mechanism, which an agent should be able to explain `[A: /best-practices]`:
- *"A floor on SPX can prevent a rug on QQQ just as much as a ceiling on SPY can prevent a rally on SPX."*
- *"A significant king node on SPY to the downside can cause whippy rejections of key nodes even if massive upside nodes exist on SPX and QQQ."*
- ***"All 3 indices can influence each other in dynamic ways — by observing nodes based on size, rate of change and position, we can gather clues as to how price will behave throughout the day."***

**Implementation:** a single call. `heat_heatmap` with `symbols=SPX,SPY,QQQ` costs **1 credit** and returns three elements in `data.symbols`. `[A: /mcp/tools]` Do not make three calls.

**The 2-of-3 relaxation (Tier C).** Multiple Tier C sources permit acting on 2/3 alignment: *"High-confidence trades require at least 2 out of 3 to align"* `[C: SPX_0DTE_Sniping_Masterclass]`; a worked 2/3 case where SPX's bearish magnet was discounted because SPY and QQQ had *"physically no room to drop"* `[C: QQQ_SPY_Floor_Bounce_Recap]`. Notably **Tier A itself uses 2/3 language** in one image caption: *"we had 2/3 confluence for longs"* `[A: /core-concepts]`. So 2/3 is defensible — but the prose rule is 3/3. Report the count and let the user set the bar.

**Tier C extension to 4 symbols:** add **IWM** for small-cap breadth `[C: Session 6]`. Legitimate as an extra `symbols` entry; not part of the documented Trinity.

**When the Trinity does *not* align — the documented alternative.** Tier A says *"stand aside."* Tier C offers a second option rather than stopping:

> *"**Workflow:** If SPY and QQQ are divergent (one bullish, one bearish) or 'muted,' stop trading indices. **Action:** Immediately pivot to Single Tickers (Stocks). When the broad market chops, relative strength/weakness in individual names becomes cleaner and more profitable."* `[C: Heatseeker 102]`

Session 7 describes the same pivot: when SPX *"doesn't look great for large flushes but won't collapse,"* shift to scanning individual tickers for names holding up against the broader market. `[C: Session 7]`

**Agent phrasing:** *"Trinity is split, so the documented guidance is to stand aside on the indices. Bootcamp material suggests an alternative — rotate to single names, where relative strength reads more cleanly during index chop."* Present it as an option, and note that the §10.5 sector filter and §12 liquidity gates still apply to whatever single name is chosen.

### 10.2 Layer 2 — GEX × VEX overlap

*"One of the most powerful confirmations in Heatseeker™ comes from **GEX/VEX confluence** — when both forces align around the same price zone."* `[A: /best-practices]`

- *"When **GEX and VEX overlap**, that zone carries **stronger influence** on price."*
- *"These alignments increase the odds of **holding or reversing** at that level."*
- *"Works exceptionally well for **swing trades** and **intra-day fades**."*
- *"Treat these as **zones of interest**, not precise levels. Use price confirmation before entering."*

Also `[A: /navigating-skylit-web-app]`: *"Watch for alignment between GEX and VEX zones — when both reinforce each other, the probability of a reaction increases significantly."*

**Implementation:** two calls (`metric=gamma`, then `metric=vanna`), 1 credit each. Intersect the high-`abs(value)` strike sets. Report overlap as a **zone**, using the §5.9 margin.

**The interpretive hierarchy (Tier C) — the two metrics are not co-equal.** Tier A says only that overlap strengthens a zone. Tier C adds a documented *sequence*: **VEX sets direction, GEX sets the entry.**

> *"**Workflow:** Use VEX to find the direction (Bull/Bear), then use GEX to find the entry (Dip into Floor)."* `[C: Heatseeker 103]`

The same source assigns explicit roles:

| Metric | Use case | Timeframe |
| --- | --- | --- |
| **GEX** | Short-term precision | 0DTE/weekly — defines the immediate floor and ceiling for entry/exit |
| **VEX** | Long-term forecasting | 2–4 weeks — reveals *"hidden"* targets dealers are hedging for next month |

`[C: Heatseeker 103]`

So for a **swing-horizon** question, run the vanna map first for bias (test for the §9.2 stair-step), then the gamma map for the level. For an **intraday** question, invert it: gamma governs (§6.11). Attribute the sequencing to bootcamp material; the co-equal-overlap framing is what Tier A actually states.

Cross-reference §6.10 for what to do when they **disagree**, and see the NFLX case study (§9.4) where gamma and vanna aligned on the same nodes — Tier C called that alignment *"brain dead obvious"* `[C: Session 5]`.

### 10.3 Layer 3 — price action primacy

The official hierarchy, stated as **Correct** vs **Incorrect** `[A: /common-pitfalls-and-mistakes]`:

**Correct:**
1. **Price Action** — always primary confirmation
2. **Heatseeker™ Confluence** — validates or strengthens the thesis
3. **Asymmetric Risk-to-Reward** — determines if the play is worth taking

**Incorrect:** Heatseeker alone · ignoring price structure · trading purely off color or value shifts.

*"The tool shows positioning, not conviction. Let price action confirm before pulling the trigger."* `[A: /common-pitfalls-and-mistakes]`

Heatseeker is **strategy-agnostic** and pairs with price-action setups, liquidity sweeps, Fibonacci retracements, breakout/breakdown strategies, and order-flow/delta analysis. *"Heatseeker™ enhances your edge by showing **where** probability clusters, not **when** to trade. It clarifies *context*, not signals."* `[A: /faqs]`

Tier C is blunt about the failure mode — *"Heat Seeker is NOT the Holy Grail"* — and supplies the aphorism ***"Nodes are magnets, but resistance is absolute"*** after two worked losses from ignoring chart structure (RDDT shorted at a 50% Fib equilibrium; TSLA held toward a 340 node while 330 midpoint resistance rejected to the cent). `[C: Session 6]` Session 7: *"Price action should always confirm first; it's number one."*

**Agent consequence:** an agent that has only the Heatseeker API has **only layer 2 of 3**. Say so. Recommend the user supply chart structure, or fetch it via Flowseeker's `underlying_chart`.

### 10.4 Layer 4 — volatility regime (VIX)

Tier B makes this a Golden Rule: **"GEX defines potential; VIX defines reality."** `[B: Greeks Bible p.2]`

The reject-vs-run checklist for climbing out of a –GEX pit `[B: Greeks Bible p.31–32]`:

| Signal | Outcome | Reason |
| --- | --- | --- |
| VIX ↓ from peak | **Run** to yellow | +VEX below supports |
| VIX ↑ on tag | **Reject** | Selling in stress |
| Close above pocket | **Run** | Resistance flipped support |
| Wick + no acceptance | **Reject** | Short-γ still active |
| Big gap to yellow | **Run** | Air pocket = fuel |
| New +GEX shelf above pocket | **Reject** | Fresh supply cap |
| Broad market strength | **Run** | External flows |
| Heavy 0DTE call selling | **Reject** | Instant ceiling |

**VIX is not in the Heatseeker API.** Fetch it separately. Tier C treats VIX as chartable — draw support/resistance, double tops, consolidations on VIX itself `[C: Futures_Clinic_Week2]` — and watches the VIX heatmap for **call walls**: *"A bright node at VIX 32 doesn't mean 'Sell Everything,' but it means volatility (range expansion) is imminent."* `[C: Heatseeker 105]`

### 10.5 Layer 5 — sector rotation (Tier C only)

Not in Tier A/B, but Session 6 elevates it to a *"core filter"* with a stated rule: **only buy calls in sectors green for the day; only buy puts in sectors red for the day.** `[C: Session 6]` Sectors tracked: SOXL/SMH, QQQ, IWM, ARK, XLV, cloud, China, solar, crypto. Session 8 adds the defensive-rotation read: *"Defensive stocks (COST, PG, WMT) pumping while semis reject = money flowing to safety."*

Tier A's nearest equivalent is Commandment 5, *"Thou shalt be aware of the broader market"* `[B: Patternpedia]`, and the documented Trinity requirement. Present sector rotation as an optional Tier C overlay. Flowseeker's `sector_flow` and `market_breadth` tools are the in-platform way to source it. `[A: /mcp/tools]`

---

## §11. Timing — when the map is readable

### 11.1 Intraday windows

| Window | Guidance | Tier / source |
| --- | --- | --- |
| **9:30–10:00 ET** | *"Do not size up… This period sees the most opening/closing of positions, causing nodes to shift rapidly."* | C: `Heatseeker 101` |
| **First 15–30 min** | Avoid fresh entries — *"this period is for dealer rebalancing and 'traps'"* | C: `SPX_0DTE_Sniping_Masterclass` |
| **~10:15 ET** | *"Wait until 10:15 AM ET for the map to stabilize before taking high-conviction trades"* | C: `Heatseeker 101` |
| **10:07–10:15 ET** | Reshuffle window reportedly **shifted** here as of late July 2025 (previously 9:30–10:00); *"still see 1-2 major reshuffles, not 3+"* | C: `Session 7` |
| **11:00–13:00 ET** | Lunch — lower volume, choppier; *"low-volume periods (like lunch hours) can distort map strength"* | **A: /limitations**; C: `Session 6` |
| **After 12:00 ET** | On high-IV days, map quality reportedly improves | C: `Session 8` |
| **15:30 ET** | **Robinhood Power Hour** auto-liquidation begins → forced flow, volatility spikes, possible full-map reshuffle | **A: /core-concepts** |
| **15:30–15:50 ET** | Observe which node is gaining control | C: `Session 6` |
| **15:50–16:00 ET** | MOC entry window | C: `Session 6` |

**The stability exception (Tier C).** *"The standard 'wait 30 minutes' rule can be bypassed **IF** there is a 'Trinity Alignment' (all 3 indices at major levels) right at the open."* `[C: Heatseeker_Recap_12-13-25]`

**Pre-market (Tier C).** *"Glitch generally ignores pre-market node taps. Volume is insufficient to confirm if a level held or broke. Wait for regular trading hours."* `[C: Heatseeker 104]`

> ⚠️ **Timing claims decay.** The 9:30–10:00 → 10:07–10:15 shift is itself evidence that this window moves. Session 7's own note: *"This could shift again; always verify latest algorithm behavior."* An agent must **date-stamp** any timing claim (these are from mid-2025) and never present them as current.

### 11.2 Calendar effects

| Event | Effect | Tier / source |
| --- | --- | --- |
| **OPEX — every 3rd Friday** | Nodes carry **less weight**; positions roll off; temporary distortions; clarity improves immediately after | **A: /core-concepts, /limitations** |
| **FOMC · CPI · JOLTS · NFP · earnings** | **Hedge nodes** appear far from price, slow-moving | **A: /core-concepts** |
| **CPI · FOMC · Power Hour** | Reshuffles *"often follow large volatility events"* | **A: /faqs** |
| **Major rebalancing** | Nodes may lose short-term influence | **A: /limitations** |
| **End of quarter** | *"Window of Weakness"* — institutional rebalancing; *"Buy the Dip fails in this regime"* | C: `Heatseeker 104` |
| **MSCI rebalancing (quarterly)** | Last 20–30 min *"pretty crazy"* | C: `Session 10` |
| **Quad Witching / OpEx** | Deep-OTM nodes *"often Hedge Nodes, not price targets"* | C: `Heatseeker 102` |
| **Weekly data rollover** | New-week data rolls over after Friday close, *"sometimes 1-2 minutes before"* — a data-vendor constraint | C: `Session 10` |

---

## §12. Data-quality gates — is this ticker even readable?

Run these **before** interpreting. Officially `[A: /limitations]`:

> *"Certain conditions reduce the clarity and reliability of dealer data:*
> - ***Low-volume periods** (like lunch hours) can distort map strength.*
> - ***Illiquid tickers** or small caps often show *noise* instead of meaningful structure.*
> - *Tickers that are set up for short squeezes can result in blowing past nodes.*
> - *During **OPEX weeks** or **major rebalancing**, nodes may lose short-term influence as contracts roll off."*

And: *"If liquidity dries up, **price moves first**, and **maps catch up** later."* `[A: /limitations]`

Best-suited instruments: **SPX, SPY, QQQ, /ES, /NQ** and other high-liquidity tickers. `[A: /intended-audience]`

### 12.1 Liquidity thresholds — Tier C, and mutually inconsistent

Report these as **community rules of thumb**, and lead with the relative framing, which is the most defensible.

| Rule | Source |
| --- | --- |
| **Relative, not absolute:** *"It all depends on the liquidity of the ticker"* — NFLX can print 77M nodes; UPST *"will never exceed 77 million because it's illiquid."* Evaluate strength **within** a ticker's own map. | `Session 5` |
| Suggested tiers: **>20–30M** reliable · **5–20M** moderate · **<5M** grain of salt | `Session 5` |
| Avoid nodes **< 4 digits**; *"if the node value is not in the millions (or high hundreds of thousands), the magnet is too weak to rely on"* | `Heatseeker 102`, `Heatseeker 103` |
| Exclude tickers with **< 4-digit** cumulative GEX; wait **2–4 weeks** after a ticker is seeded before trading it | `Session 6` |
| Named problem tickers: LULU (data too fresh, July 2025), ACN (*"not enough data seated"*), UPST, SNOW, ISRG, Wingstop (*99k notional*), COST, MELI, CELH | `Sessions 5, 6, 10`; `Heatseeker 102, 103` |
| *"Dealers are notoriously bad at hedging Meme Stocks"* — maps less reliable where retail flow overrides dealer positioning | `Heatseeker 104` |
| **Do not trade NDX options** — bid/ask spread cited at $1400–1800; trade QQQ instead | `Heatseeker 103` |
| Avoid tickers at **all-time highs** — no overhead structure, false breakouts likely | `Heatseeker 102`, `Session 10` |

> ⚠️ **Session 5 explicitly retracts the absolute thresholds:** *"Remove any documentation suggesting 'nodes above 5M are always reliable' and replace with 'context-dependent confidence scoring.'"* If you cite a millions-threshold, cite this retraction with it.

**The API gives you no units for `value`** (§3.4). So the operationally safe test is **relative**: rank `abs(value)` within one symbol's own `strikes[]` and report the ratio of the King to the median node. Do not compare raw `value` across symbols.

### 12.2 Agent pre-flight checklist

Adapted from the Skylit-published checklist `[A: mintlify://skills/skylit]`, with API mechanics added:

- [ ] **Heatmap is current** — check `asOf`. Is it recent relative to the question asked?
- [ ] **`meta.mode`** — `live` or `historical`? Say which.
- [ ] **`meta.cached`** — if `true`, the data may be up to 5s stale (`Cache-Control: max-age=5`).
- [ ] **`meta.metric`** — is this the GEX or VEX view the user meant?
- [ ] **Credits sufficient** — `X-Credits-Remaining > 0` and enough for planned calls.
- [ ] **Confluence** — if SPY/SPX/QQQ, do all three agree?
- [ ] **Price action aligns** — does the thesis match what price is doing? (Often outside your data — say so.)
- [ ] **Node is untouched** — for bounce reads, prefer untested nodes.
- [ ] **R:R is asymmetric** — if close to 1:1, the documented guidance is to skip.
- [ ] **Map hasn't reshuffled** — compare to the last check.
- [ ] **No OPEX / Power Hour interference** — is it OPEX week or near 15:30 ET?
- [ ] **Liquidity** — is `abs(value)` at the King meaningfully above the map's median?
- [ ] **API key not exposed** — never echo a key into output.

---

## §13. Limitations and failure modes

Lead with Tier A. This section exists so an agent states caveats *accurately* rather than hedging generically.

### 13.1 Official limitations

*"While **Heatseeker™** provides a powerful view into **dealer positioning**, it's not a crystal ball (although many still believe it to be). It shows how **Market Makers (MMs)** are *currently* exposed to the market, **not what will happen next**."* `[A: /limitations]`

| Limitation | Description | Mitigation |
| --- | --- | --- |
| **Not 100% accurate** | Displays positioning, not forecasts. *"**Random events**, such as news headlines, earnings reports, or geopolitical shocks, can instantly alter dealer flows."* | Combine with price-action confirmation |
| **Market conditions** | Low liquidity distorts accuracy | Avoid low-volume periods |
| **Map reshuffles** | Levels shift without warning; nodes can *"**vanish**, **weaken**, or **reappear** at new levels intraday"* | Reassess after volatility events |
| **Overreliance** | Tool ≠ signal | Use with confluence and structure |

Two quotes worth reproducing `[A: /limitations]`:
> *"Treat Heatseeker™ as a **map of influence**, not a prophecy of price."*
> *"Heatseeker™ is **contextual intelligence**, not a trading signal. Used correctly, it reveals how market dealers shape the battlefield, but **you** are still the general deciding when to strike."*

On R:R over accuracy: *"Focus on **risk-to-reward**, not just 'being right.' A strong R:R setup can remain profitable even if only **1 out of 5** plays succeed."* `[A: /limitations]`

### 13.2 Official common mistakes

`[A: /common-pitfalls-and-mistakes]`

| Mistake | Description | Solution |
| --- | --- | --- |
| Over-trusting Heatseeker™ | Using it as a signal system | Let price action and levels lead |
| Ignoring confluence | Misalignment across SPX/SPY/QQQ | Wait for alignment |
| Ignoring map reshuffles | Trading outdated data | Re-evaluate after shifts |
| Forcing symmetry | Expecting perfect reactions | Think in zones, not lines |
| Emotional trading | Bias, revenge trades, FOMO | Follow system, not emotion |

*"Traders often expect perfect reactions, exact retests or mirror-image moves. But real dealer behavior is **organic and adaptive**, not geometric."* `[A: /common-pitfalls-and-mistakes]`

### 13.3 Official gotchas from the published agent skill

`[A: mintlify://skills/skylit]` — this list is written *for* agents, so treat it as directly binding:

- **Treating Heatseeker as a signal generator.** *"It's context, not a trade signal."*
- **Ignoring map reshuffles.** *"Don't cling to old levels — pause, observe, and re-evaluate."*
- **Trading without confluence.** *"Confluence = confidence."*
- **Forcing symmetry.** Allow 5–10 points of margin. Think zones.
- **Overtrading midpoints.** *"Risk-to-reward is roughly 1:1 — no edge."*
- **Retesting weak nodes.** *"Prioritize untouched nodes for bounce plays."*
- **Committing API keys to source control.**
- **Exceeding stream concurrency limits** (5 max) → `429 stream_limit_reached`.
- **Running out of credits mid-stream** → monitor the `credits` event.
- **Misinterpreting flow side.** *"'Side' (bid/mid/ask) describes where activity occurred relative to the market, not perfect proof of intent."* (Flowseeker-specific.)
- **Ignoring OPEX week.**
- **Forgetting about Power Hour.**

### 13.4 What breaks it — Tier C

Consistent across many files; useful for setting expectations honestly.

- **Success rate is claimed at 80–85%, never higher.** *"I've always considered it… an 80% or 85% success rate"* `[C: Session 10]`; *"80-85% hit rate, but non-zero chance it all goes wrong"* `[C: Session 1]`. Session 10 adds: *"Never claims 90-100%… 15-20% trades will fail to plan; external factors matter."* **This figure appears nowhere in Tier A — always attribute it to bootcamp material, never to Skylit.**
- **News overrides the map.** Worked example: a put floor at 6000 predicted a bounce; fresh Iran-sanctions headlines knifed price ~15 points through it. *"There's no trading strategy that's gonna work for something like this."* `[C: Sessions 0, 1]`
- **Earnings, tweets, black swans.** *"Heatseeker is literally best in class in terms of the existing tools that are out there… but it's still no match against earnings reports… it's no match against like Trump tweets."* `[C: Session 5]`
- **Macro overrides micro.** A ticker's node target can be blocked by an index-level move. *"Must monitor overall market conditions and sector conditions in parallel."* `[C: Session 10]`
- **Fresh-data tickers.** Insufficient seeded history → unreliable node placement. `[C: Session 6]`
- **High-IV regimes.** VIX gapping 15–20% → *"maps become noisy; too many competing nodes; difficulty reading clear setup."* `[C: Session 8]`
- **Regime dependence.** *"Heatseeker isn't gonna do the best… in a day like today"* — one session contrasts a favorable *opening-range* day against an unfavorable *trapped-range* day, but **never defines the filter quantitatively**. Session 9's own write-up flags this as *"critical missing documentation."* `[C: Session 9]` Do not invent the threshold.

### 13.5 Platform-reliability notes (Tier C, and likely dated)

- *"Heat map rarely breaks; when it does, only stalls 1-3 minutes. Self-healing process in place with alerting system."* `[C: Session 3]`
- *"Heat map nodes update in near-real-time (milliseconds)… not 15-minute delayed."* But at that time, *"the price indicator on the platform uses 15-minute delayed data (limitation due to data vendor package; fixing in progress)."* `[C: Session 5]`
- A *"Heatseeker Light Channel"* is described as delayed ~6–7 minutes, *"for reference only."* `[C: Session 7]`

These are mid-2025 operational snapshots from a fast-moving beta product. **Do not present them as current behavior.** The authoritative current statement is the API's own: 1-second time series, `Cache-Control: max-age=5` (§2.4).

---

## §14. Quantitative reference — the only numbers you may quote

**Rule: if a number is not in this table, do not state it.** Every row is traceable. Tier A/B rows are safe to state plainly; Tier C rows require attribution.

### 14.1 API and platform (Tier A — safe to state)

| Quantity | Value |
| --- | --- |
| MCP endpoint | `https://mcp.skylit.ai/mcp` |
| REST base URL | `https://api.skylit.ai` |
| Web app | `https://app.skylit.ai` |
| MCP tool count | **40** per `/mcp/tools` (38 on `/mcp/overview` + changelog — likely stale). Call `tools/list`. |
| Heatseeker MCP tools | **2** (`heat_heatmap`, `heat_historical_heatmap`) |
| `/v1/heatmap` cost | **1 credit** |
| `/v1/historical` cost | **5 credits** |
| `/v1/stream` cost | **1 on connect + 1 per minute open** |
| `/v1/openapi.json` cost | **0 credits** |
| New-account credits | **5,000** |
| Rate ceiling | **600 requests/minute** |
| `maxStrikes` | default **92**, min **1**, max **400** |
| Historical lookback | up to **365 days** |
| Underlying resolution | **1-second time series** |
| Recommended live poll interval | **every 60–90 s** |
| Historical replay grid | any **minute** boundary |
| Response cache | `Cache-Control: private, max-age=5` |
| `meta.resolution` example | `"1m"` |
| Concurrent SSE streams | **5 per customer per pod** |
| SSE connection recycle | **~1 hour** (`reconnect` event) |
| SSE keepalive comment | every **30 s** |
| SSE credits event | every **minute boundary** |
| `metric` enum | `gamma` \| `vanna` (default `gamma`) |
| `nodeType` enum | `king` · `gatekeeper` · `pika` · `barney` · `significant` · `normal` |
| `mode` enum | `live` \| `historical` |

### 14.2 Interpretation (Tier A / B — safe to state, cite the page)

| Quantity | Value | Source |
| --- | --- | --- |
| SPX deflection margin | **~5–10 points** | A: /core-concepts |
| SPX node margin, general | **5–10 points** | A: /common-pitfalls-and-mistakes |
| Validity example | a rejection **5½ points** from the King is still valid | A: /core-concepts |
| Retest: 1st touch | strongest, highest probability | A: /core-concepts |
| Retest: 2nd touch | **~66%** | A: /core-concepts |
| Retest: 3rd+ touch | **~33%** | A: /core-concepts |
| Midpoint R:R | *"roughly **1:1**"* → no edge | A: /core-concepts |
| Minimum viable R:R | if *"close to 1:1, skip the trade"* | A: /best-practices |
| Profitability framing | a strong R:R setup can profit even if **1 of 5** plays succeed | A: /limitations |
| OPEX cadence | every **3rd Friday** of the month | A: /core-concepts |
| Robinhood liquidation start | **3:30 PM EST** | A: /core-concepts |
| Learning curve | Wk **1–2** node patterns/color · Wk **3–4** reactions & reshuffles · Month **2+** pattern recognition | A: /faqs |
| Learning curve, summary | *"**3–4 weeks** to internalize flow"* | A: /faqs |
| Trend skew comparison | *"the 3 nodes above spot dwarf that of the 3 nodes below"* (**N=3**) | B: Patternpedia/pattern-trend |
| Gamma world | **0–5 DTE** | B: Greeks Bible |
| Vanna world | **7–180 DTE** | B: Greeks Bible |
| Cross-expiry: speed | **0–2 DTE** | B: Greeks Bible |
| Cross-expiry: drift | **7–30 DTE** | B: Greeks Bible |
| Cross-expiry: gravity | **45–90 DTE+** | B: Greeks Bible |
| GEX-dominant tenors | 0DTE & 1DTE | B: Greeks Bible |
| VEX-dominant tenors | 5–30 DTE | B: Greeks Bible |
| Strike lifecycle deltas | far OTM Δ≈**0** · ATM Δ≈**0.5** · deep ITM Δ→**±1** | B: Greeks Bible |
| Documented outcome, 11/7/25 topping case | SPY 685→**661**, QQQ low **598**, *"nearly **3%** move peak to trough"* | B: Patternpedia/topping-bottoming |
| NFLX decoy node distance | **5.5%** from spot, over **4** trading sessions | B: Patternpedia/case-study |
| NFLX decoy node ratio | ~**3×** the value of the 1232.5 gatekeeper | B: Patternpedia/case-study |
| LLY prior run | ~**150 points** in a week with only **2** consolidation days | B: Patternpedia/case-study |

### 14.3 Practitioner figures (Tier C — **must be attributed**)

| Quantity | Value | Source |
| --- | --- | --- |
| Claimed success rate | **80–85%**, *"never 90-100%"* | Sessions 1, 10 |
| SPX overshoot margin | **±5 points** | Heatseeker 101; Crash case study |
| SPY / QQQ overshoot margin | **±$0.50** | Crash case study |
| Node-velocity signal | ~**50%** growth while price is flat → becoming dominant magnet | Heatseeker 101; Recap 12-13-25 |
| Node-velocity window cited | *"**10-minute** rate of increase"*, +**20%** pre-move | Crash case study |
| Gatekeeper sizing | **1/4 to 1/3** of the King node's size | Heatseeker 105 |
| Node liquidity tiers | >**20–30M** reliable · **5–20M** moderate · <**5M** skeptical (**retracted in the same session**) | Session 5 |
| Minimum node magnitude | **≥4 digits**; prefer millions | Heatseeker 102, 103; Session 6 |
| New-ticker seasoning | **2–4 weeks** before trading | Session 6 |
| Min R:R, intraday | **3:1** | SPX_0DTE_Sniping_Masterclass; Session 7 |
| Min R:R, stated inverted | **1:3** | Session 1 |
| Min R:R, Mon–Wed | **3:1 to 5:1** | Session 10 |
| Min R:R, swings | **7:1** | Heatseeker 103 |
| Min R:R if win rate >70% | **2:1** | Session 7 |
| Range-consumption filter | skip if >**50%** of the range is already consumed | Session 1 |
| Accumulated-node bounce probability | **90–95%** | Session 9 |
| Double bottom / triple bottom | **66%** / **33%** | Session 10 |
| Level over-testing | **5th** test of a level likely breaks | Session 7 |
| Level over-testing | 4th+ test *"probability effectively zero"* | Session 10 |
| Position size cap | **10%** of account per trade | Session 0 |
| Conviction sizing tiers | **2.5% / 5% / 10%** | Session 1 |
| Daily lockouts | **2** losses = stop; **3** trades = stop; **10+** win streak = skip next day | Session 1 |
| Profit target | **20–40%** exits | Session 0 |
| Profit target | **30–50%** minimum, *"below 30% is still losing to theta"* | Session 6 |
| Mandatory partial | at **+100%**, take **50%** off | Sessions 5, 6 |
| Max hold | **6 hours** | Session 6 |
| Runner eligibility | account **≥$15K** | Session 5 |
| Trinity relaxation | **2 of 3** indices aligned | SPX_0DTE_Sniping_Masterclass |
| High-IV protocol trigger | VIX gap **15–20%** → reduce size, wait until after **12:00 ET** | Session 8 |
| Lunch avoidance | **11:00–13:00 ET** | Session 6 |
| MOC windows | observe **15:30–15:50**, enter **15:50+** | Session 6 |
| Morning reshuffle window | **9:30–10:00 ET**, shifted to **10:07–10:15 ET** ~Jul 2025 | Heatseeker 101; Session 7 |
| Map stabilization | wait until **10:15 ET** | Heatseeker 101 |
| Shuffles per day | **0–3+** | Session 5 |
| ES ↔ SPX offset | ES trades ~**8–10 points** above SPX, and the spread fluctuates | Session 0 |
| Reps to build confidence | **1,000–5,000** repetitions; **3–12 months** | Session 7 |
| Unlearning phase | **2–3 years** if carrying old habits | Session 5 |

### 14.4 Futures contract specs (Tier C — adjacent, verify independently)

`[C: Futures_Clinic_Week1]`

| Contract | Value / point | Value / tick | Leverage |
| --- | --- | --- | --- |
| ES (E-mini S&P) | $50 | $12.50 | ~1:50 |
| NQ (E-mini Nasdaq) | $20 | $5.00 | ~1:20 |
| MES (Micro S&P) | $5 | $1.25 | 1/10 ES |
| MNQ (Micro Nasdaq) | $2 | $0.50 | 1/10 NQ |

Session timings given in ET: Asia 19:00–02:00 · London 02:00–08:00 · New York 08:30–16:00 · London/NY overlap 08:30–11:00/12:00. The stated bridge concept: ***"Heatseeker tells us WHY; Futures tells us WHERE"*** — use Heatseeker for structural pressure on SPX/SPY/QQQ, execute on ES/NQ.

**These are third-party contract facts, not Skylit data.** Verify against the exchange before relying on them.

---

## §15. Conflicts and unresolved items

An agent that surfaces these earns trust. An agent that silently picks one loses it.

### 15.1 MCP tool count

| Page | Says |
| --- | --- |
| `/mcp/overview` | *"All **38** tools, grouped, with credit costs"* |
| `/platform/changelog` | *"**38 tools** spanning scored options trades, sweeps, tide…"* — a **launch release note** |
| `/mcp/tools` | *"The server exposes **40 tools**"* |
| `mintlify://skills/skylit` | *"The MCP server exposes **40** tools"* |

**Likely explanation:** 38 is the launch-time count preserved in the changelog and echoed in a stale card on `/mcp/overview`; 40 is the current count on the canonical catalog page and in the published agent skill. **Resolution: prefer 40 if you must state a number, but the correct behavior is to call `tools/list` and report what the server returns.**

### 15.2 Node retest probabilities — the biggest disagreement in the corpus

| Claim | Source | Tier |
| --- | --- | --- |
| 1st strongest · 2nd **~66% holds** · 3rd+ **~33% holds** | /core-concepts | **A** |
| *"probability of holding is **~66% for the first two tests**"*; avoid the 3rd/4th | Heatseeker 101 | C |
| 1st high · 2nd **~66% holds** · 3rd **~66% chance of BREAKING** | Heatseeker 104 | C |
| Nodes hold 1st/2nd touch; by 3rd/4th the node is *"suspect and highly likely to flush"* | Crash case study | C |
| Accumulated node zones bounce **90–95%** of the time | Session 9 | C |
| Double bottom **66%**, triple bottom **33%**; 4th+ *"effectively zero"* | Session 10 | C |
| Tests 1–4 may work; **5th** likely breaks | Session 7 | C |

**Note the direction flip:** Tier A's 33% is *probability of reversal*, while Heatseeker 104's 66% is *probability of breaking* — those are compatible (~1/3 hold ≈ 2/3 break). Session 9's 90–95% is the genuine outlier, but it is scoped to **accumulated clusters**, not single nodes, which may be a different population.

**Resolution:** quote **Tier A (66% / 33%)**. If pressed, explain the reconciliation above and note the cluster caveat.

### 15.3 Risk-to-reward minimum
Ranges from 2:1 to 7:1 across Tier C (§14.3), including one apparent transposition (*"1:3"* in Session 1 vs *"3:1"* elsewhere — both describe the same asymmetry, written in opposite conventions). Tier A only ever states the **qualitative** rule: *"If risk and reward are close to 1:1, skip the trade."* `[A: /best-practices]` **Resolution: state the Tier A rule; present numeric minimums as varying by timeframe and source.**

### 15.4 Morning reshuffle window
9:30–10:00 ET `[C: Heatseeker 101]` vs 10:07–10:15 ET *"as of late July 2025"* `[C: Session 7]`. Session 7 explicitly says the timing *"has been flipped/switched"* and *"could shift again."* Meanwhile `Heatseeker 103` describes scanning during 9:30–10:00. **Resolution: not in Tier A at all. Present as an undocumented, time-varying observation and date-stamp it.**

### 15.5 Node "wash-off" vs node "resuscitation"
- **Wash-off:** once a target node is tapped, exposure *"washes off"*; don't re-trade it. `[C: Heatseeker 104]`
- **Resuscitation:** diminished nodes are just experiencing delta decay and *"come back to life"* if price returns. `[C: SPX_0DTE_Sniping_Masterclass, Crash case study]`

**Resolution:** Tier A supplies the tiebreaker as an *observable*, not a rule — after delivery, a **decreasing** node means low reversion probability; an **increasing** node means higher. `[A: /core-concepts]` So: don't assume either; **measure `velocityPct` at the node.**

### 15.6 Keyboard shortcuts
`Ctrl+G` / `Ctrl+V`, docs-specified for **Windows** `[A: /navigating-skylit-web-app]` vs `Command+G` / `Command+V` `[C: Heatseeker 103]`. Likely a platform difference; only Windows is documented. **Resolution: give both, attribute both.**

### 15.7 Node liquidity thresholds
Absolute millions-thresholds `[C: Session 5]` vs *"4-digit"* cumulative-GEX rules `[C: Sessions 6, Heatseeker 102/103]` vs Session 5's own retraction in favor of relative scoring. Compounded by the API publishing **no units** for `value`. **Resolution: use relative ranking within a single symbol's map. Cite the retraction if quoting a threshold.**

### 15.8 VEX expansion
*"Vanna Exposure"* `[A: /navigating-skylit-web-app]` and API enum `vanna` `[A: /openapi]` vs *"Volatility Exposure"* `[C: Heatseeker 103]`. **Resolution: Vanna Exposure.**

### 15.9 "Wait for confirmation" vs "enter before confirmation"

A direct doctrinal conflict, and the one most likely to trip an agent, because Tier A and Tier C give opposite instructions.

| Position | Claim | Tier |
| --- | --- | --- |
| **Wait** | *"Let **price action** confirm your setups"*; *"Let price action confirm before pulling the trigger"*; validation step 2 is *"**Price Reaction:** Wait for confirmation or rejection wick"* | **A**: /best-practices, /common-pitfalls-and-mistakes, /faqs |
| **Don't wait** | *"Waiting for confirmation (e.g., a candle close or trendline break) ensures you have a symmetric (1:1) risk/reward. You must enter **before** confirmation (at the level) to get the 5:1 or 10:1 risk/reward required for long-term profitability."* | C: Heatseeker 106 |
| **Don't wait** | *"When you're trading with heat seeker, you cannot trade with fear. You cannot wait for confirmation. You wait for confirmation, you're a reactive trader that doesn't have a forecast into what price is going to do."* — reframes Heatseeker as *"a forecasting tool"* rather than a reactive one | C: Session 5 |
| **Don't wait** | *"The entries that you want to take are the entries that every other retail trader out there is afraid to take"*; *"Heatseeker IS the confirmation, not price action"* | C: Session 7 |

**The disagreement is real, not a paraphrase artifact** — three independent Tier C files argue the no-confirmation position, and they argue it *because* waiting collapses R:R toward the 1:1 that Tier A itself calls edgeless (§5.5).

**Resolution for an agent:** state the **Tier A** rule — price action confirms first, and it sits above Heatseeker in the documented hierarchy (§10.3). If the user raises the contrarian-entry argument, acknowledge it accurately as community coaching doctrine, explain its stated rationale (confirmation costs you the asymmetry), and note that it is a **risk-tolerance choice the user must make**, not a factual dispute you can settle. Do not advocate either side.

### 15.10 Missing from this corpus
- **`BootcampSession4_transcript.md`** — topic *"Glitch Algorithm & Glitch Dots"*. Glitch Dots are referenced as a confluence signal in Sessions 7–8 (one file cites a *"55% hit rate on daily"* on DJT) but **never defined here**. Treat as out of scope.
- **Flowseeker endpoint schemas** — the `/api-reference/flow/*`, `/contract/*`, `/underlying/*` etc. pages exist on docs.skylit.ai but were not retrieved in depth. Field-level Flowseeker questions require fetching those pages.
- **Atlas "Orbs"** and Atlas drawing presets — referenced in `/atlas/overview`, not captured.
- **`nodeType` classification thresholds** — never published.
- **The `velocityPct` window length** — never published.
- **Units for `value`** — never published.
- **A quantitative "market regime" filter** — Session 9 asks for one; nobody supplies it.

---

## §16. Agent workflows

Copy-ready recipes. Each states its credit cost.

### 16.1 Single-symbol read — 1 credit

```
heat_heatmap { symbols: "SPY" }
```

Then:
1. Record `asOf`, `spot`, `meta.metric`, `meta.mode`, `meta.cached`.
2. Sort `strikes` by `abs(value)` descending.
3. **King** = `nodeType == "king"` (fallback: `max(abs(value))`). Note `sign(value)`.
4. **Ceiling** = max `abs(value)` where `strike > spot`. **Floor** = max `abs(value)` where `strike < spot`.
5. **Gatekeepers** = `nodeType == "gatekeeper"`, or high-`abs(value)` nodes between `spot` and the King.
6. **Range** = floor → ceiling. **Midpoint** = mean. Flag the midpoint as a documented trap (§5.5).
7. **Air pockets** = contiguous low-`abs(value)` spans (§5.6). Note the sign of the surrounding nodes.
8. **Velocity** = strikes with the largest `abs(velocityPct)` — where dealers are actively repositioning.
9. Apply the §5.9 margin. Report levels as zones.
10. Classify against §9 patterns. If it matches **Rainbow Road**, say the map is unreadable.

### 16.2 Trinity read — 1 credit

```
heat_heatmap { symbols: "SPX,SPY,QQQ" }
```

Run §16.1 per element, then:
1. Build a per-symbol bias: is the larger-`abs(value)` side above or below spot? Is the King above or below?
2. Count agreement. **3/3** = documented highest-probability condition. **2/3** = Tier C relaxation, report the count. **1/3 or split** = *"if one diverges, stand aside."*
3. Check for the documented cross-index blocks: does a floor on one prevent a rug on another? Does a ceiling on one cap a rally on another? (§10.1)
4. Report the confluence count explicitly. Never bury it.

### 16.3 GEX + VEX confluence — 2 credits

```
heat_heatmap { symbols: "SPX,SPY,QQQ" }                  # metric defaults to gamma
heat_heatmap { symbols: "SPX,SPY,QQQ", metric: "vanna" }
```

1. Per symbol, collect high-`abs(value)` strikes from each metric.
2. **Overlap** = strikes appearing in both, within the §5.9 margin → *"that zone carries stronger influence."*
3. **Disagreement** → classify against the §6.10 alignment table and name a likely cause from the four listed.
4. Apply the tenor rule: **gamma governs intraday, vanna governs across days** (§6.11).
5. On the VEX map, test for the **stair-step** pattern (§9.2) — that is the documented multi-day topping/bottoming read.

### 16.4 Detect a reshuffle — 2 credits (live) or 10 (historical pair)

Live: call `heat_heatmap`, wait ≥60 s (§2.4), call again. Historical: two `heat_historical_heatmap` calls at different `at` values.

Compare and report:

| Check | Signal |
| --- | --- |
| `king` strike moved | **Reshuffle** — restate the new anchor |
| `gatekeeper` appeared or vanished | Structural change on the path to the King |
| Top-3 by `abs(value)` reordered | Positioning shift |
| Ceiling value ↓ **and** ceiling strike ↓ | **Rolling of ceilings** → bearish confirmation (§8.3) |
| Floor value ↓ **and** floor strike ↑ | **Rolling of floors** → bullish confirmation (§8.3) |
| Node values rising broadly | **Accumulation** |
| Node values falling broadly | **Dissipation** |
| Price direction opposes node-value direction | **Divergence** (§8.4) — label Tier C |

If a reshuffle is detected: *"pause and reassess… avoid trading until the new structure stabilizes"* `[A: /faqs]`. Do not carry the prior read forward.

### 16.5 Historical replay / backtest — 5 credits per instant

```
heat_historical_heatmap { symbols: "SPY", at: "2026-03-05T14:30:00Z" }
```

- `at` is **required** and RFC3339, up to 365 days back.
- `velocityPct` is **absent** — compute change yourself across two instants.
- `404 no_data` means no snapshot at/near that instant — widen or shift, don't retry identically.
- Grid at **minute boundaries** for a 1-minute replay (§2.4).
- **Cost discipline:** a 1-hour minute-by-minute replay is 60 calls × 5 = **300 credits**. Budget before looping; state the cost to the user first.

### 16.6 Live monitoring

**Preferred — SSE (REST only, no MCP tool):** `GET /v1/stream?symbol=SPY`. 1 credit on connect + 1/min. Handle `velocity_update`, watch `credits`, and reconnect on `reconnect`. Max 5 concurrent (§2.5).

**Fallback — polling:** `heat_heatmap` every **60–90 s** = 40–60 credits/hour per call. Faster polling returns cached data (`max-age=5`) and wastes credits.

### 16.7 Pair with Flowseeker — the documented power move

The official pattern `[A: /mcp/examples]`:

> *"SPY has a gamma wall at 600 — is the flow defending it or pushing through?"*
> `heat_heatmap` (locate the wall) → `flow_strikes` / `flow_tide` around that strike to see whether premium is accumulating into or fading away from it.

Positioning from Heatseeker; intent from Flowseeker. Additional documented pairings:
- Gamma walls now → `heat_heatmap`
- Gamma profile at a past open → `heat_historical_heatmap` with `at`
- Is OTM call activity new positioning or closing? → `vol_oi` + `unusual_oi`
- Overall market tone → `market_breadth`, then `market_overview` / `market_tide`
- Strongest sector call flow → `market_breadth` → `sector_flow`

**Prompt-quality note from the docs:** *"Agents work best when you give them a ticker and a timeframe. 'TSLA, last hour' beats 'show me some flow.' If a result looks empty, ask the agent to widen the window or lower the `min_premium` filter."* `[A: /mcp/examples]`

### 16.8 Error handling

| Condition | Do this |
| --- | --- |
| `401` | Key missing/invalid. Ask the user to check it. **Never** print the key. |
| `402 insufficient_credits` | Stop calling. Report the balance and the cost of what remains. |
| `403` | Key revoked/expired, account suspended, **or the plan lacks API access.** Name all three possibilities. |
| `404 no_data` (historical) | No snapshot near `at`. Suggest a different instant; do not retry identically. |
| `404` (unknown symbol) | Verify the ticker. `flow_search` resolves underlyings. |
| `429` + `Retry-After` | Back off for the stated interval. |
| `429 stream_limit_reached` | Already 5 streams open. Close one. |
| `503` | Heatmap temporarily unavailable. Retry with backoff; tell the user it is upstream. |
| MCP session-budget error | The gateway's per-session call budget was hit. Report it; do not loop. |
| `tools/list` empty | You skipped `notifications/initialized`. Redo the handshake (§2.1). |

---

## §17. Output templates

### 17.1 Standard heatmap read

```markdown
**{SYMBOL} — {GEX|VEX} · spot {spot} ({priceChange:+.2f}, {priceChangePercent:+.2f}%)**
Snapshot {asOf} · mode {live|historical}{ · cached}
Expirations contributing: {expirations joined}

| Role | Strike | Value | nodeType | velocityPct |
|---|---|---|---|---|
| King | … | … | king | … |
| Ceiling | … | … | … | … |
| Gatekeeper(s) | … | … | gatekeeper | … |
| Floor | … | … | … | … |

- **Range:** {floor}–{ceiling}. Midpoint {mid} — documented low-edge zone, R:R ≈ 1:1.
- **Character:** {King is positive/negative} → {low-vol absorption / high-vol amplification + overshoot risk}.
- **Air pockets:** {span or "none"} — {sign} gamma, so transit likely {sharp / mild}.
- **Velocity:** {strikes moving fastest, and direction} → {accumulation / dissipation}.
- **Pattern:** {Patternpedia name or "no clean match"}.
- **Margin:** treat levels as zones (±5–10 pts on SPX; ±$0.50 SPY/QQQ per bootcamp material).

**Missing for a full read:** price action / market structure (documented as the *primary* layer),
{VEX if only GEX pulled}, {Trinity if single-symbol}, VIX regime.
```

### 17.2 Trinity confluence

```markdown
**Trinity read — snapshot {asOf}**

| Symbol | Spot | King | Bias | Floor | Ceiling |
|---|---|---|---|---|---|
| SPX | … | … | … | … | … |
| SPY | … | … | … | … | … |
| QQQ | … | … | … | … | … |

**Agreement: {n}/3.**
- 3/3 → documented highest-probability condition.
- 2/3 → bootcamp-material relaxation; official rule is all three.
- <2/3 → *"if one diverges, stand aside."*

**Cross-index notes:** {e.g. "SPY floor at X may prevent the QQQ rug toward Y"}
```

### 17.3 Reshuffle report

```markdown
**Structural change: {asOf_1} → {asOf_2}**

| Change | Before | After |
|---|---|---|
| King | … | … |
| Gatekeepers | … | … |
| Floor | … | … |
| Ceiling | … | … |

**Read:** {Rolling of floors → bullish confirmation | Rolling of ceilings → bearish confirmation |
Accumulation | Dissipation | Reshuffle}

Official guidance on a reshuffle: dealers have adjusted risk — pause, mark the new King and
Gatekeepers, and avoid trading until the structure stabilizes.
```

### 17.4 Data-quality warning

```markdown
⚠️ **Reliability caveats for {SYMBOL} at {asOf}:**
- {Liquidity: King abs(value) is only Nx the map median — Skylit documents illiquid tickers as
  showing "noise instead of meaningful structure".}
- {OPEX week (3rd Friday) — nodes carry less weight as contracts expire.}
- {Within 30 min of 15:30 ET — Robinhood auto-liquidation can spike volatility and reshuffle the map.}
- {Lunch hours — low volume "can distort map strength".}
- {meta.cached = true — data may be up to 5s stale.}
```

---

## §18. Guardrails

### 18.1 Reproduce the source's own disclaimer when giving substantive analysis

*The Greeks Bible — Skylit Edition* opens with this, and it is the platform's own framing `[B: Greeks Bible p.1]`:

> *"The information provided in this guide… is for **educational purposes only**. It is **not financial advice**, nor an offer, solicitation, or recommendation to buy, sell, or hold any security, derivative, or investment instrument. Options and derivatives trading involve substantial risk, including the potential loss of your entire investment. Past performance, market behavior, or model outputs (e.g., GEX/VEX/Heatseeker data) **do not guarantee future results**… Always conduct your own due diligence and consult with a licensed financial professional before making any trading decisions."*
>
> *"**TL;DR: This is not advice. It's education.** Markets are risky. Trade responsibly."*

### 18.2 Agent conduct rules

**Do:**
- Describe what the map shows, name the structures, explain the dealer mechanism.
- Frame everything as **probability zones and context**, matching `/best-practices`.
- Cite `asOf` on every read.
- Name the evidence tier when the number matters.
- State what you could **not** verify — especially price action and VIX, which are outside this API.
- Volunteer the §12/§13 caveats when they apply, unprompted.

**Don't:**
- Issue directives to buy, sell, or size a position. The tool is *"NOT a signal bot"* `[A: /intended-audience]`.
- Recommend position sizes, stop placement, or leverage. The Tier C corpus contains extensive sizing and no-stop-loss doctrine (`Sessions 0, 1, 5, 6, 8, 10`) — you may **describe** it as the source's guidance if asked, always attributed, never as your own recommendation.
- Quote a success rate as Skylit's. **80–85% is bootcamp material and appears nowhere in Tier A.**
- Assert cent-precise reversals. Documented margin is 5–10 points on SPX.
- Invent a node, a threshold, a velocity window, or a unit for `value`.
- Print, log, or echo an API key.
- Present mid-2025 Tier C timing or platform-latency claims as current.

### 18.3 Consistent honesty about scope

An agent holding only the two `heat_*` tools should say plainly what it has and lacks:

> *"I can read dealer positioning per strike, plus live velocity. I can't see price action, market structure, VIX, volume, or open interest — and Skylit's own documentation puts price action **above** Heatseeker in the confirmation hierarchy. So treat this as one of three required layers, not a complete read."*

---

## §19. Glossary

Terms marked **[official]** appear in Tier A/B. Terms marked *(community)* appear only in Tier C — attribute them.

| Term | Definition |
| --- | --- |
| **Accumulation** [official] | Dealers building positions → stronger magnet zones |
| **Air Pocket** [official] | Zone of low volume / small nodes price moves easily through; sharper through negative gamma |
| **Atlas** [official] | Skylit charting product that overlays Heatseeker / Flowseeker / dark-pool layers on the chart |
| **Barney** [official] | `nodeType` for **negative** gamma exposure. Dark purple. *"Gasoline on a fire"* — amplifies movement |
| **Charm** [official] | Delta's sensitivity to time; drives afternoon rebalancing drift |
| **Deflection** [official] | Price rejecting/reversing at a node rather than passing through |
| **Delivery / Price Delivery** [official] | Price having already touched a node and moved away — reduces that node's influence |
| **Dissipation** [official] | Dealers closing positions → weakening nodes |
| **Drive-Away** [official] | MMs pushing price off the King Node when it arrives too early in the session |
| **F-Map** *(community)* | Untradeably noisy map, 5+ conflicting nodes. Prefer the official name **Rainbow Road** |
| **Flip Zone** [official] | Strike where GEX or VEX polarity changes sign; crossing it flips dealer hedging direction |
| **Flowseeker** [official] | Skylit's options-flow product; `flow_*` and screener MCP tools |
| **Gatekeeper** [official] | `nodeType` for a node blocking the path to the King. *"Bouncers at the door of a nightclub"* |
| **GEX** [official] | Gamma Exposure. API `metric=gamma` |
| **Glitch Dot** *(community)* | Adjacent multi-timeframe reversal signal. **Not Heatseeker. Undefined in this corpus** (Session 4 missing) |
| **Hedge Exhaustion** [official] | Deltas saturate at ±1 → dealer hedging stops → liquidity vacuum → reversal fuel |
| **Hedge Node** [official] | Large protective position far from price, slow-moving, appearing around macro events. *"Insurance rather than an active magnet"* |
| **King Node** [official] | `nodeType` for the highest-absolute-value node; MM settlement target EOD/EOW. **Color-independent** |
| **Magnet** [official] | Core metaphor: nodes attract price, and can also repel it |
| **Midpoint** [official] | Center of a range. *"Market Makers' favorite trap zones"*; R:R ≈ 1:1 |
| **Nexus** [official] | Skylit's social-trading layer; documentation *"coming soon"* |
| **Node** [official] | A strike's exposure value on the heatmap |
| **Normal** [official] | `nodeType` for low-value nodes, minimal influence |
| **OPEX** [official] | Monthly options expiration, every 3rd Friday. Nodes carry less weight |
| **Pika** [official] | `nodeType` for **positive** gamma exposure. Bright yellow. *"Magnetic pillow"* — absorbs movement |
| **Pin Job** [official] | MMs pinning price near the King Node, typically late session |
| **Pro-cyclical** [official] | Dealer hedging that **amplifies** the move (short gamma) |
| **Queen Node** *(community)* | Informal name for the 2nd-largest node. **Not in the `nodeType` enum** |
| **Reshuffle** [official] | Rapid change in dealer exposure producing new map structure |
| **Rolling of Ceilings** [official] | Upside values fall **and** the ceiling moves to a lower strike → bearish confirmation |
| **Rolling of Floors** [official] | Downside values fall **and** the floor moves to a higher strike → bullish confirmation |
| **Rug Setup** [official] | Patternpedia: yellow node stacked above a purple node with no floor below |
| **Significant** [official] | `nodeType` for mid-range value nodes; secondary interest |
| **Stacked Nodes** [official] | Opposite-sign exposures adjacent; a *"spring under tension"*. Also a Tier A priority tier: *"strong bounce from those levels"* |
| **Stair-Step** [official] | VEX pattern of monotonic accumulation across successive expirations → multi-day directional posture |
| **Trinity** [official] | The SPX + SPY + QQQ cross-check. One `heat_heatmap` call: `symbols=SPX,SPY,QQQ` |
| **Velocity** [official] | Node rate of change. API `velocityPct`, **live only**, window undocumented |
| **VEX** [official] | **Vanna** Exposure. API `metric=vanna` |

---

## §20. Source manifest

### 20.1 docs.skylit.ai — Tier A

Retrieved **2026-07-25** via the Mintlify documentation MCP server at `https://docs.skylit.ai/mcp` (tools: `search_skylit`, `query_docs_filesystem_skylit`, `submit_feedback`; resource: `mintlify://skills/skylit`). Note: `docs.skylit.ai/mcp` is the **documentation** MCP; the product MCP is `mcp.skylit.ai/mcp`.

**Read in full:** `/introduction` · `/intended-audience` · `/core-concepts` · `/how-to-read-and-use-heatseeker` · `/best-practices` · `/common-pitfalls-and-mistakes` · `/limitations` · `/faqs` · `/resources` · `/navigating-skylit-web-app` · `/examples-and-case-studies` · `/api-reference/introduction` · `/api-reference/authentication` · `/api-reference/heatmap/live-per-strike-heatmap-one-or-more-symbols` · `/api-reference/heatmap/replay-per-strike-heatmap-at-a-past-instant-one-or-more-symbols` · `/api-reference/heatmap/live-sse-stream-one-symbol-per-connection` · `/openapi/openapi.yaml` · `/mcp/overview` · `/mcp/quickstart` · `/mcp/tools` · `/mcp/examples` · `/help-page/general-information` · `/help-page/written-guides` · `/nexus/overview` · `/platform/overview` · resource `mintlify://skills/skylit`

**Read partially:** `/atlas/overview` (expiration-column section; "Orbs" and drawing presets not captured) · `/flowseeker/overview` (surface table and Live Feed)

**Not retrieved:** ~60 `/api-reference/*` Flowseeker endpoint pages (`analytics/`, `contract/`, `dark-pool/`, `flow/`, `history/`, `market/`, `meta/`, `ratios/`, `scoring/`, `sector/`, `sweeps/`, `symbols/`, `underlying/`) · `/atlas/drawing-presets` · `/platform/changelog` · `/platform/support` · `/help-page/useful-threads` · `/help-page/video-links` · `/openapi/flowseeker-openapi.yaml` · `/openapi/atlas-openapi.yaml`

Full doc tree confirmed as **23 directories, 94 files** via `tree / -L 3`.

### 20.2 Local corpus — 32 files in `./extracted-text/`

**Tier B — Patternpedia & Skylit written curriculum (9 files)**

| File | Notes |
| --- | --- |
| `The Ten Commandments of Using Heatseeker _ Patternpedia _ Skylit Docs.md` | §7.3 |
| `PATTERN_ The Whipsaw _ Patternpedia _ Skylit Docs.md` | §9.1 |
| `PATTERN_ Rainbow Road _ Patternpedia _ Skylit Docs.md` | §9.1 |
| `PATTERN_ The Gatekeeper _ Patternpedia _ Skylit Docs.md` | §9.1 |
| `PATTERN_ Trend _ Patternpedia _ Skylit Docs.md` | §9.1 |
| `PATTERN_ RUG SETUP _ Patternpedia _ Skylit Docs.md` | by John Wicks · §9.1 |
| `CASE STUDY_ SPECULATIVE AND DECOY NODES _ Patternpedia _ Skylit Docs.md` | §9.1 |
| `TOPPING PATTERNS _ BOTTOMING PATTERNS_ _ Patternpedia _ Skylit Docs.md` | by John Wicks & warren · §9.2 |
| `TheGreeksBibleSkylit Edition.md` | 39 pp · §6 · educational-use-only disclaimer |

**Tier C — course & webinar analyses (11 files)**

`2026-02-04_Skylit_x_Owls_Heatseeker_101.md` · `_102.md` · `_103_Swing.md` · `_104_Macro.md` · `_105_Trump_Dump.md` · `_106_Advanced.md` · `2026-02-04_SPX_0DTE_Sniping_Masterclass_Analysis.md` · `2026-02-04_SPX_3000_Percent_Crash_Case_Study.md` · `2026-02-04_Heatseeker_Recap_12-13-25.md` · `2026-02-04_QQQ_SPY_Floor_Bounce_Recap_01-14-26.md` · `2026-02-04_Futures_Clinic_Week1_Intro_Prop_Firms.md` · `2026-02-04_Futures_Clinic_Week2_Chart_Literacy_TA.md`

Each names a YouTube source URL and is headed *"This analysis covers…"* — derived summaries, not transcripts.

**Tier C — bootcamp session analyses (10 files)**

| File | Dated | Topic |
| --- | --- | --- |
| `BootcampSession0_transcript.md` | Jun 8, 2025 | Risk management, psychology, position sizing; divergence signal; node pinching |
| `BootcampSession1_transcript.md` | Jun 9, 2025 | Psychology, kill switches, R:R, scaling vs averaging down |
| `BootcampSession2_transcript.md` | Jun 15, 2025 | Liquidity hunts, Fibonacci, price action |
| `BootcampSession3_transcript.md` | Jun 22, 2025 | Greeks hierarchy, strike selection, balanced vs skewed maps |
| **`BootcampSession4_transcript.md`** | — | **ABSENT.** Topic per Session 5: *"Glitch Algorithm & Glitch Dots"* |
| `BootcampSession5_transcript.md` | Jul 13, 2025 | Magnets formalized, equal-pull midpoints, node liquidity, Skylit roadmap |
| `BootcampSession6_transcript.md` | Jul 20, 2025 | Price-action primacy, pivotal nodes, sector rotation, fresh-data exclusion |
| `BootcampSession7_transcript.md` | Jul 27, 2025 | Reshuffle-timing shift, divergence, contrarian entries |
| `BootcampSession8_transcript.md` | Aug 3, 2025 | Deflection trading, node-strength comparison, VIX regime, F-maps |
| `BootcampSession9_transcript.md` | Aug 13, 2025 | Node accumulation, yellow vs purple behavior, regime filter (undefined) |
| `BootcampSession10_transcript.md` | Aug 31, 2025 | Deflection vs king-node chasing, R:R by weekday, macro overrides |

Each names a `.vtt` recording as its source and is headed *"Transcript Analysis"* — derived summaries, not transcripts.

### 20.3 Named individuals

From `/help-page/general-information` (Tier A): **Glitch** — Heatseeker founder (@Glitch_Trades) · **Giul** — approved analyst (@Simply0DTE) · **John Wicks** — success team (@The_John_Wicks) · **Nicog8** — success team (@nicog8_trading).

Authorship (Tier A, `/help-page/written-guides`): **Skylit Docs** by John Wicks · **Patternpedia** by Warren and John Wicks.

Additional names appearing **only** in Tier C material, as instructors or participants: *Jimin*, *AAMDylan*, *Nico*, *Michael*, *Chris K.*, *Deeplay*, *Beastmode777*, *Guillermo*, *Thanson*, *John Nicho*. This document makes **no claim** about how these map to the Tier A team list, and an agent should not speculate.

### 20.4 Reproducing the doc retrieval

The docs MCP is public and unauthenticated. To refresh:

```bash
curl -sS -X POST https://docs.skylit.ai/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call",
       "params":{"name":"query_docs_filesystem_skylit",
                 "arguments":{"command":"cat /core-concepts.mdx"}}}' \
  | sed -n 's/^data: //p' | jq -r '.result.content[0].text'
```

Supported commands inside that sandbox: `rg`, `grep`, `find`, `tree`, `ls`, `cat`, `head`, `tail`, `stat`, `wc`, `sort`, `uniq`, `cut`, `sed`, `awk`, `jq`. Output truncates at **30 KB per call**; each call is **stateless** (cwd resets to `/`). Also available: `https://docs.skylit.ai/llms.txt` for page-by-page navigation.

---

## Appendix A — one-screen cheat sheet

```
CALL             heat_heatmap {symbols:"SPX,SPY,QQQ"}            1 credit, Trinity in one call
                 heat_heatmap {symbols:"SPY", metric:"vanna"}     1 credit, VEX view
                 heat_historical_heatmap {symbols, at}            5 credits, ≤365d back
                 GET /v1/stream?symbol=SPY                        REST only, 1 + 1/min, max 5

POLL             every 60–90s. Underlying is 1s; cache is 5s. Faster = wasted credits.

RANK             by abs(value), NOT sign, NOT color, NOT nodeType alone.
                 Then read sign(value) separately for character.

nodeType         king · gatekeeper · pika(+γ, yellow) · barney(−γ, purple) · significant · normal
PRIORITY         King > Gatekeeper > Clusters > Double-Stacked

READ  1 magnets → 2 King → 3 range → 4 gatekeepers → 5 flow (accum / dissip / reshuffle)

CHARACTER        pika/+  → low vol, smooth, "magnetic pillow", pins
                 barney/− → high vol, wicky, "gasoline on fire", OVERSHOOTS before reversing

MECHANISM        +GEX: dealers buy dips / sell rips  → stabilizing, mean-reverting
                 −GEX: dealers sell dips / buy rips  → amplifying, trending
                 +VEX: buy as IV falls, sell as IV rises
                 −VEX: sell as IV falls, buy as IV rises
                 Loops die at Δ saturation (±1) → hedge exhaustion → reversal fuel
                 0–2 DTE = speed(γ) · 7–30 = drift(vanna) · 45–90+ = gravity(vol regime)
                 GEX = brakes. VEX = slope. VIX = weather.

BETWEEN NODES    Most common state, and the hardest. Spot between two comparable magnets
                 "vibrates — not trends". Report a tug-of-war, NOT a direction.
                 Escapes only when one magnet weakens (IV shift or OI decay).           §6.5

FLAT GEX MAP     Not automatically "no read". If gamma is structureless but vanna isn't,
                 the market is vol-driven → trade the regime, not direction. Check vanna
                 BEFORE calling it Rainbow Road.                                        §6.10

STRONGEST SIGNAL Same strike flips sign in BOTH gamma and vanna = confirmed flip zone
                 = breakout trigger. Beats any single-metric flip.                      §6.10

METRIC ORDER     Swing horizon: VEX for direction → GEX for entry        [Tier C]
                 Intraday: gamma governs                                 [Tier B]       §10.2

MARGIN           SPX ±5–10 pts [official] · SPY/QQQ ±$0.50 [bootcamp]. Zones, not lines.
RETEST           1st strongest · 2nd ~66% · 3rd+ ~33%   [official; Tier C disputes — see §15.2]
MIDPOINT         R:R ≈ 1:1 → no edge. Fade edges instead.
DELIVERED-FROM   node value ↓ → low reversion odds · node value ↑ → higher reversion odds

TWO-SNAPSHOT     ceiling value↓ + strike↓ = rolling of ceilings → bearish confirm
                 floor   value↓ + strike↑ = rolling of floors   → bullish confirm
                 price ↑ while lower floor grows = divergence → expect retrace [Tier C]

CONFLUENCE       Trinity 3/3 (2/3 = Tier C relaxation) · GEX∩VEX overlap · price action FIRST
HIERARCHY        1 Price Action → 2 Heatseeker → 3 Asymmetric R:R      [official]
IF TRINITY SPLIT official: stand aside. Tier C alternative: pivot to single tickers,
                 where relative strength reads cleaner during index chop.               §10.1

DERATE WHEN      OPEX week (3rd Fri) · 15:30 ET Power Hour · lunch · illiquid ticker ·
                 fresh-seeded ticker · VIX spike · meta.cached=true · post-reshuffle

ALWAYS REPORT    asOf · spot · meta.metric · meta.mode · confluence count · evidence tier
NEVER            invent a node/threshold/unit/velocity window · quote 80–85% as Skylit's ·
                 give cent-precise levels · issue buy/sell/sizing directives · echo an API key
```

---

*End of reference. Tier A/B claims are traceable to the pages and files listed in §20; Tier C claims are attributed inline. Where sources disagree, §15 records the disagreement rather than resolving it silently.*
