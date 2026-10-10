```text
██████╗ ██╗ ██████╗████████╗██████╗  █████╗ ██████╗ ███████╗███████╗
██╔══██╗██║██╔════╝╚══██╔══╝██╔══██╗██╔══██╗██╔══██╗██╔════╝██╔════╝
██████╔╝██║██║  ███╗  ██║   ██████╔╝███████║██║  ██║█████╗  ███████╗
██╔══██╗██║██║   ██║  ██║   ██╔══██╗██╔══██║██║  ██║██╔══╝  ╚════██║
██████╔╝██║╚██████╔╝  ██║   ██║  ██║██║  ██║██████╔╝███████╗███████║
╚═════╝ ╚═╝ ╚═════╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝ ╚══════╝╚══════╝
      │         ┃                 tape ─────────────────────────────
     ┃█┃   │   ┃█┃       14:32:05.123  SELL  120 @ 7830.00  ┐
     ┃█┃  ┃░┃  ┃█┃   │   14:32:05.123  SELL   86 @ 7830.00  │
      │   ┃░┃   │  ┃░┃   14:32:05.123  SELL  140 @ 7829.75  ├─▶ ES big SELL 501
          ┃░┃      ┃░┃   14:32:05.123  SELL  155 @ 7829.50  ┘
           │        │    14:32:06.200  BUY     3 @ 7829.75
                                        one sweep, one alert  ·  by v01
```

> **⚠️ Caution: do not place Topstep trades from a VPS or cloud server.**
> Topstep requires all trading activity to come from your own personal device.
> VPS, VPN and remote-server trading on trading accounts is prohibited and can get
> an account suspended or removed. Connecting an account from a server without
> trading is fine, and this tool only reads market data, so running it on a
> server is fine. If you wire it into a bot that places orders, run that
> bot at home. See Topstep's rules:
> [TopstepX API Access](https://help.topstep.com/en/articles/11187768-topstepx-api-access)
> and the [Terms of Use](https://www.topstep.com/terms-of-use).

**One alert per aggressive sweep on the ProjectX (TopstepX) futures tape.**

Built first as a signal source for trading bots: each finished sweep is handed
to your code as a typed object you can use as a level, a confluence input or a
filter. Posting the same alerts to a Discord channel is a second, equally good
use for it.

`License: AGPL-3.0-or-later` · `Python 3.11+` · `Author: v01`

- Discord username: v01
- Project identifier: bigtrades-indicator
- Kind: api

## Skylit surface

- Access: none yet
- Products: none

This project reads the ProjectX (TopstepX) futures tape, not Skylit data. It
is shared here because big trades work well next to Skylit levels: a sweep that
prints at a Heatseeker node or a key level adds confluence to that level.

---

## What it does

A large market order rarely prints as one trade. It sweeps through several
resting orders and the exchange reports a burst of small prints with the same
side and (nearly) the same timestamp. If you alert on the first print that
crosses your size threshold, you report a partial number (say 206) while the
order is still filling up to 501.

`bigtrades` listens to the ProjectX market hub, stitches those prints back
into one **sweep**, and publishes it once it is finished:

```text
ES big SELL 501 @ 7830.00→7829.50 | avg fill 7829.80 | 6 prints | 14:32:05.123 UTC
```

- **Sweep aggregation**: same side, exchange timestamp equal (or within `join_ms`),
  closed by a side flip, a timestamp gap, or `quiet_ms` of silence.
- **Two publish modes**: `final` (default, full size) or `first_qualify`
  (fastest, the size at the moment the threshold is crossed).
- **Front-month auto-resolve** through ProjectX contract search (`ES`, `NQ`, `MNQ`, ...).
- **Sinks**: feed your own bot through a callback, print to the console, or post
  to a Discord webhook. A new sink takes a few lines.
- **Raw tape recorder** (off by default) and an offline **replay** command.
- Small, typed, tested, no network in tests.

## Quickstart

```bash
git clone <this repo> bigtrades-indicator && cd bigtrades-indicator
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # add your ProjectX username + API key
pytest                      # all offline
bigtrades run               # live
```

Replay a recorded tape (no credentials, no network):

```bash
bigtrades replay tests/fixtures/tape_es_sweep.jsonl
```

## Configuration

All settings are environment variables (a `.env` file is loaded automatically).

| Variable | Default | Meaning |
|---|---|---|
| `PROJECTX_USERNAME` | — | Your platform username (not your email). |
| `PROJECTX_API_KEY` | — | API key from TopstepX (Settings → API). |
| `PROJECTX_API_URL` | `https://api.topstepx.com` | REST base URL. |
| `PROJECTX_MARKET_HUB_URL` | `https://rtc.topstepx.com/hubs/market` | Market hub. |
| `PROJECTX_LIVE_DATA` | `false` | Contract search against live (`true`) or sim data. |
| `BIGTRADES_SYMBOLS` | `ES,NQ` | Roots (front month resolved) or explicit ids like `CON.F.US.EP.Z26`. |
| `BIGTRADES_THRESHOLDS` | `ES:200,NQ:100` | **Example values only.** `ROOT:SIZE`, `*` = catch-all. |
| `BIGTRADES_JOIN_MS` | `0` | Max exchange-time gap between prints of one sweep. `0` = identical timestamps. |
| `BIGTRADES_QUIET_MS` | `300` | Close a sweep after this much silence for that root. |
| `BIGTRADES_PUBLISH_MODE` | `final` | `final` or `first_qualify`. |
| `BIGTRADES_TZ` | `UTC` | Time zone of the timestamp in the alert (IANA name). |
| `DISCORD_WEBHOOK_URL` | empty | Optional. Posts `content` only; never overrides the webhook's name or avatar. |
| `BIGTRADES_TAPE_DIR` | empty (off) | Record every hub trade message to `<dir>/YYYY-MM-DD.jsonl`. |

> The thresholds above are neutral examples so the tool does something out of
> the box. They are not a recommendation. Pick sizes that mean something for
> your market and session.

### Thresholds per ticker

Every instrument needs its own threshold. A size that is huge on NQ is ordinary
on ES, and micros (MNQ, MES) trade in much bigger clip counts than the full-size
contracts, so one number for everything will either spam you or stay silent.

- Set one `ROOT:SIZE` pair per symbol you watch, for example
  `BIGTRADES_THRESHOLDS=ES:200,NQ:100,MES:500,MNQ:500,*:50` (example values only).
  `*` is the catch-all for any root you did not list.
- Tune it on your own data: record a few sessions with `BIGTRADES_TAPE_DIR`,
  replay them, and pick a size that fires a handful of times per session rather
  than every minute.
- Revisit it when volatility or volume changes a lot. The overnight session is
  thinner than the cash session, so the same size means more there.

## Example output

```text
2026-10-08 16:32:05 INFO bigtrades.app: watching ES (CON.F.US.EP.Z26), threshold 200
2026-10-08 16:32:05 INFO bigtrades.app: watching NQ (CON.F.US.ENQ.Z26), threshold 100
ES big SELL 501 @ 7830.00→7829.50 | avg fill 7829.80 | 6 prints | 14:32:05.123 UTC
NQ big BUY 118 @ 25410.25 | avg fill 25410.25 | 3 prints | 14:35:41.870 UTC
```

With `BIGTRADES_PUBLISH_MODE=first_qualify` the first line becomes:

```text
ES big SELL 206 @ 7830.00 | avg fill 7830.00 | 2 prints | 14:32:05.123 UTC | first qualify (may still be filling)
```

## Connecting to ProjectX (TopstepX Gateway API)

Official docs: <https://gateway.docs.projectx.com/docs/intro>

> **Tip: use code `topstep` for 50% off the ProjectX API subscription.** The code
> was valid when this was published and the offer may change. Use code `TS-NNZ8GY3DB3DH2` for account purchases if you are also buying an account to trade. 

1. **Get access.** API access is a paid ProjectX subscription. Once it is active,
   sign in to TopstepX and open **Settings → API**
   (`https://topstepx.com/settings?tab=api`) to create, reveal or revoke a key.
2. **Log in** with your platform *username* and the key:
   ```http
   POST https://api.topstepx.com/api/Auth/loginKey
   {"userName": "your-username", "apiKey": "your-key"}
   → {"token": "<JWT>", "success": true, "errorCode": 0}
   ```
   A failed login is still HTTP 200, so always check `success` / `errorCode`.
3. **Use the token** as `Authorization: Bearer <JWT>` on every REST call.
   It is valid for 24 hours. Refresh it before then with
   `POST /api/Auth/validate` (returns `newToken`), or log in again.
   One token works for all REST calls and hub connections.
4. **Find the contract.** `POST /api/Contract/search {"searchText": "ES", "live": false}`
   returns ids like `CON.F.US.EP.Z26` (note: ES is `EP`, NQ is `ENQ`).
   This project picks the nearest active expiry for you.
5. **Open the market hub** (SignalR over WebSocket):
   `wss://rtc.topstepx.com/hubs/market?access_token=<JWT>`, then invoke
   `SubscribeContractTrades(contractId)`. The server calls `GatewayTrade`
   with `(contractId, data)` where `data` is one trade or a list:
   ```json
   {"symbolId": "F.US.EP", "price": 7830.0, "timestamp": "2026-10-08T14:32:05.123Z", "type": 1, "volume": 120}
   ```
   `type` is the aggressor (`0` buy, `1` sell). Other market-hub feeds:
   `SubscribeContractQuotes` → `GatewayQuote`, `SubscribeContractMarketDepth` → `GatewayDepth`.
   The **user hub** (`/hubs/user`) carries `GatewayUserAccount/Order/Position/Trade`
   for your own account.
6. **Be polite.** Documented limits: 200 requests / 60 s for most endpoints,
   50 / 30 s for `History/retrieveBars`. Over the limit you get HTTP 429; back off.
   Log in once and reuse the token instead of logging in per request.

The minimal client lives in `src/bigtrades/projectx/` (REST + a ~150 line
SignalR JSON-protocol client) if you want to reuse it.

## Using it in a trading bot

This is the main use. Pass your own sink and every finished sweep arrives as a
`BigTrade` with side, size, price range, avg fill, print count and timestamps:

```python
import asyncio

from bigtrades.app import run
from bigtrades.config import Settings
from bigtrades.models import BigTrade
from bigtrades.sinks import CallbackSink


async def on_big_trade(trade: BigTrade, text: str) -> None:
    # Your logic: store trade.avg_price as a level, add confluence weight,
    # filter entries by trade.side, and so on.
    print(trade.root, trade.side, trade.size, trade.avg_price)


asyncio.run(run(Settings.from_env(), extra_sinks=[CallbackSink(on_big_trade)]))
```

Console output (and Discord, if `DISCORD_WEBHOOK_URL` is set) keeps working
alongside your callback. If your bot already has its own ProjectX connection,
feed its prints to `SweepAggregator` in `aggregator.py` directly; it is pure and
has no I/O.

## Writing your own sink

```python
from bigtrades.models import BigTrade


class CsvSink:
    def __init__(self, path: str) -> None:
        self.path = path

    async def publish(self, trade: BigTrade, text: str) -> None:
        with open(self.path, "a") as f:
            f.write(f"{trade.end.isoformat()},{trade.root},{trade.side},{trade.size},{trade.avg_price:.2f}\n")
```

Pass it as `run(settings, extra_sinks=[CsvSink("trades.csv")])`.

## Project layout

```text
src/bigtrades/
  aggregator.py   sweep logic (pure, deterministic)
  gateway.py      GatewayTrade -> TapePrint
  format.py       alert text
  sinks.py        Sink protocol, console, Discord
  tape.py         recorder + replay
  config.py       env settings
  app.py          live wiring
  cli.py          `bigtrades run | replay`
  projectx/       REST client, contract helpers, SignalR client
tests/            offline tests + fixtures
AGENTS.md         full spec for AI agents
```

## FAQ

**Why does my size differ from another platform's "big trades" bubble?**
Platforms aggregate differently (timestamp tolerance, quiet time, whether they
cap at one price level). Try `BIGTRADES_JOIN_MS=1..5` if your feed spreads one
sweep over a few milliseconds, and record the tape to compare.

**`final` or `first_qualify`?** `final` tells you the real size about
`quiet_ms` later. `first_qualify` is earlier but under-reports large sweeps.

**Does this place orders?** No. It only reads public market data.

**Do I need a funded account?** You need ProjectX API access. Contract search
works with sim data (`PROJECTX_LIVE_DATA=false`).

**Is the size cumulative?** Yes, within one sweep: every print of the same
aggressive order is added up into one size, which is why it can match the big
trade bubbles on ATAS. It does not add up separate sweeps over a rolling time
window.

**Windows?** Yes. Use `.venv\Scripts\activate`. Ctrl+C stops it.

## Disclaimer

This is not financial advice. It is an educational tool provided as is, with
no warranty. Trading futures carries substantial risk of loss. Test on a
simulated or practice account first and use it at your own risk. The author is
not affiliated with ProjectX, Topstep, or Discord.

## License

Copyright (C) 2026 v01

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option) any
later version. See [LICENSE](LICENSE).

AGPL means: if you modify it and let others use it, including over a network
(a bot, a web service), you must share your modified source under the same
license.
