# AGENTS.md: bigtrades-indicator

Instructions for AI coding agents. This file is a complete spec: an agent
should be able to rebuild the project from scratch with it, or change it
without breaking the contracts below.

License: AGPL-3.0-or-later. Author credit: `v01`. Every source file starts with:

```python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 v01
```

## 1. Purpose

Read the ProjectX (TopstepX) market-hub trade tape for a few futures roots and
publish **one alert per aggressive sweep** whose total size meets a per-root
threshold. Output goes to pluggable sinks (console, Discord webhook).
Read-only: the program never places orders.

## 2. Architecture

```text
.env ─▶ Settings ─┐
                  ▼
ProjectXClient (REST, httpx) ── loginKey/validate ──▶ JWT
   │  Contract/search ─▶ pick_front_month ─▶ {root: contractId}
   ▼
HubConnection (SignalR JSON over websockets) ── SubscribeContractTrades(contractId)
   │  GatewayTrade(contractId, data)
   ▼
parse_gateway_trade ─▶ TapePrint* ─▶ SweepAggregator.on_print(p, now_ms) ─▶ BigTrade*
                                       ▲ flush(now_ms) every 50 ms (quiet close)
                                       │
TapeRecorder (optional, raw JSONL)     ▼
                               format_big_trade ─▶ publish_all(sinks)
```

Modules (`src/bigtrades/`):

| Module | Responsibility | I/O |
|---|---|---|
| `models.py` | `Side` (StrEnum BUY/SELL), `TapePrint`, `BigTrade` (frozen, slots) | none |
| `aggregator.py` | `Thresholds`, `SweepAggregator` | none (pure) |
| `gateway.py` | `parse_timestamp_us`, `parse_gateway_trade` | none |
| `format.py` | `fmt_price`, `format_big_trade` | none |
| `sinks.py` | `Sink` protocol, `ConsoleSink`, `CallbackSink`, `DiscordWebhookSink`, `publish_all` | stdout / HTTP |
| `tape.py` | `TapeRecorder`, `read_records`, `replay` | files |
| `config.py` | `Settings.from_env` | env |
| `app.py` | `resolve_contracts`, `BigTradesApp`, `run` | network |
| `cli.py` | `bigtrades run`, `bigtrades replay PATH` | — |
| `projectx/client.py` | async REST client, token lifecycle, 429 backoff | HTTP |
| `projectx/symbols.py` | contract id parsing, root aliases, front month | none |
| `projectx/signalr.py` | SignalR framing + reconnecting hub client | WebSocket |

## 3. Data contracts

### GatewayTrade (input)
Hub invocation `{"type":1,"target":"GatewayTrade","arguments":[contractId, data]}`.
`data` is a dict or a list of dicts:

| field | type | notes |
|---|---|---|
| `symbolId` | str | e.g. `F.US.EP` |
| `price` | number | > 0 |
| `timestamp` | ISO-8601 str | may have 7 fractional digits; Z or offset |
| `type` | int | TradeLogType: 0 Buy, 1 Sell (aggressor). Anything else: drop |
| `volume` | number | > 0, cast to int |

### TapePrint
`root: str, contract_id: str, price: float, size: int, side: Side, ts_us: int`
(`ts_us` = exchange time, µs since epoch, UTC; integer so equality is exact).

### BigTrade (output)
`root, contract_id, side, size, first_price, last_price, low, high, avg_price,
prints, start_ts_us, end_ts_us, mode` where `mode ∈ {"final","first_qualify"}`.

### Alert text
```text
{root} big {BUY|SELL} {size:,} @ {first}[→{last}] | avg fill {avg_price} | {n} print[s] | {HH:MM:SS.mmm TZ}
```
Prices: at least 2 decimals, more only if needed. The size-weighted average fill price (avg fill) uses the same number
of decimals as the prices. `first_qualify` appends ` | first qualify (may still be filling)`.

### Discord payload
`{"content": text[:2000], "allowed_mentions": {"parse": []}}`. Never send
`username` or `avatar_url`. On 429 wait `retry_after` (max 10 s), retry ≤ 2.

### Tape file
One JSON object per line: `{"recv_ms": float, "arguments": [contractId, data]}`.
Replay also accepts a bare GatewayTrade object per line.

## 4. Algorithm

```text
state: open[root] -> Sweep | absent
Sweep: side, contract_id, first/last/low/high price, size, notional, prints,
       start_ts_us, last_ts_us, last_arrival_ms, published

on_print(p, now_ms):
    out = []
    s = open.get(p.root)
    if s and not extends(s, p): out += close(s); s = None
    if s is None: s = start(p, now_ms); open[p.root] = s
    else: s.add(p, now_ms)          # size += p.size, notional += price*size, prints += 1,
                                    # last_ts = max(last_ts, p.ts), last_arrival = now_ms
    if mode == first_qualify and not s.published and s.size >= threshold(root):
        s.published = True; out += [snapshot(s, "first_qualify")]
    return out

extends(s, p) = p.side == s.side and p.contract_id == s.contract_id
                and |p.ts_us - s.last_ts_us| <= join_ms * 1000

flush(now_ms):  for each open s with now_ms - s.last_arrival_ms >= quiet_ms: close(s)
close(s):       remove; if mode == final and s.size >= threshold(root): emit snapshot(s, "final")
close_all():    close every open sweep (shutdown, end of replay)
threshold(root) = sizes[root] or sizes["*"] or None (None never qualifies)
```

The arrival clock (`now_ms`) is monotonic wall time live and `recv_ms` (or
exchange time if absent) in replay. Never read the clock inside the aggregator.

### Edge cases (all covered by tests or documented)
- Prints of one sweep can arrive in several hub messages: state lives across calls.
- Batched messages (list `data`) are processed in order.
- Equal timestamps, opposite side: closes the sweep (side flip).
- Out-of-order timestamp within `join_ms`: still joins (absolute difference).
- A late print after a quiet close starts a new sweep.
- Unknown root with no `*` threshold: aggregated but never published.
- Different contract ids for the same root (roll): treated as different sweeps.
- Shutdown: `close_all()` publishes qualifying open sweeps.

## 5. ProjectX specifics

- REST base `https://api.topstepx.com`; every response has `success`, `errorCode`,
  `errorMessage`. HTTP 200 with `success:false` is an error.
- `POST /api/Auth/loginKey {"userName","apiKey"}` → `token` (24 h).
  `POST /api/Auth/validate` → `newToken`. Refresh after 20 h; on 401 re-login once.
- `POST /api/Contract/search {"searchText","live"}` → `contracts[]` (≤ 20),
  each `{id, name, description, tickSize, tickValue, activeContract, symbolId}`.
- Contract id `CON.F.US.<code>.<M><YY>`; aliases `EP→ES`, `ENQ→NQ`.
  Front month = rows with exact root match, active first, then (year, month) ascending.
- Hub: `wss://rtc.topstepx.com/hubs/market?access_token=<JWT>`, skip negotiate,
  handshake `{"protocol":"json","version":1}\x1e`, ping `{"type":6}` every 15 s,
  `{"type":7}` = server close. Re-subscribe after every reconnect.
- Rate limits: 200 req / 60 s (bars: 50 / 30 s). Back off on 429 (honour `Retry-After`).

Docs: https://gateway.docs.projectx.com/docs/intro ,
https://gateway.docs.projectx.com/docs/realtime/

## 6. Test plan (all offline)

| Test | Asserts |
|---|---|
| split prints, same ts | one trade, size 501, avg fill, low/high, prints |
| side flip | closes and publishes the sell sweep only |
| quiet close | not at 299 ms, yes at 300 ms |
| join 0 vs join 5 | 1 ms apart splits at 0; chain within 5 ms joins, 12 ms gap closes |
| first_qualify vs final | 206 at crossing vs 501 at close, never both |
| independent roots, catch-all `*`, close_all | as named |
| GatewayTrade parsing | dict/list, 7-digit fraction, bad rows dropped |
| replay fixture | 501 in final, 206 in first_qualify; exact text prefix |
| recorder | off by default; recorded file replays to the same result |
| REST client | loginKey body, bearer header, validate after 20 h, error body raises |
| Discord sink | payload is content + allowed_mentions only |
| app | hub message → sink; non-trade targets ignored |

HTTP is faked with `httpx.MockTransport`. No test opens a socket.

## 7. Conventions

- Python 3.11+, `from __future__ import annotations`, full type hints.
- Pure logic has no I/O and no clock reads; inject `now_ms` / `clock`.
- Dataclasses `frozen=True, slots=True` for values.
- ruff (`E,F,I,B,UP,SIM`), line length 120, black-compatible formatting.
- Never log secrets (API key, JWT, webhook URL).
- Keep dependencies to `httpx`, `websockets`, `python-dotenv`.
- Thresholds in docs and defaults are labelled examples.
