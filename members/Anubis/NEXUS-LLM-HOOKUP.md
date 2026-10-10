# Nexus auto-trader — build guide for an LLM or a coder

*Give this whole file to an LLM (as a system prompt or as context), or to a developer. It describes how to connect a
trade signal to Nexus, the simulated options board, and how to manage each position. It holds no key, no secret,
no account data and no connection detail. You supply the connection through the plug functions in §3.*

---

## 0. Instructions to the LLM that reads this file

- You build or run an options auto-trader for **Nexus**. Nexus is a **simulated** board. No real money moves.
- You MUST call the outside world only through the plug functions in §3. Do not invent a URL, a route, a header or
  a field name. If a plug is missing, stop and ask the human for it.
- You MUST NOT write, print, log or store a key, a token or a password. The plug functions hold all access.
- You MUST follow the rules in §4 to §7 exactly. Do not add a rule. Do not tune a number.
- If an order or an exit is refused, record the reason and continue. Do not retry in a loop.
- Nexus takes **options only**. Never send shares, futures or a short sale.

---

## 1. What the system does

1. A **signal source** says: "this name, this side (up or down), entry price X".
2. When the live price of the name is inside the entry band, the trader picks **one option contract** on the side
   of the signal (a call for up, a put for down).
3. The trader sizes the order from its **own** budget and sends it to Nexus.
4. Every 20 seconds, the trader checks the option's price and applies the **exit ladder**: stop at −80%, sell half
   at +20%, sell the rest at +30%. All three are **% of the option premium**, never % of the stock.
5. Each fill and each exit goes to a log and, if wanted, to a chat message.

```
signal ──► entry check ──► pick contract ──► size ──► placeOrder ──► open position
                                                                         │
                         every 20 s: getOptionMid ──► exit ladder ──► exitTrade
```

---

## 2. Words used in this file

| word | meaning |
|---|---|
| contract | One option: underlying root, strike, call or put, expiry date. |
| premium / mid | The option price. Mid = (bid + ask) / 2. One contract = 100 × the premium in dollars. |
| entry price (OTE) | The underlying price where the signal wants to enter. |
| entry band | How close the live price must be to the entry price. Example: 0.25% → enter only when \|price − entry\| / entry ≤ 0.0025. |
| lane | `swing` (multi-day plays) or `intra` (same-day 0DTE plays). Each lane has its own budget. |
| return `r` | `mid_now / entry_fill − 1`. +0.20 = the option is up 20%. |
| position | One open Nexus trade that the trader tracks: id, contract, quantity left, fill price, trimmed yes/no. |

---

## 3. The plug functions (you connect these — nothing else talks to the outside)

Write each plug once, with your own access. The trader logic in §4–§7 calls only these.

```ts
// A contract in neutral form. The plug converts it to whatever the venue needs.
type Contract = { root: string; strike: number; right: 'C' | 'P'; expiry: string /* YYYY-MM-DD */ };

// 1. Send a buy-to-open market order to Nexus.
//    Returns the trade, or throws an Error whose message says why it was refused.
placeOrder(c: Contract, qty: number, tag: string):
  Promise<{ tradeId: string; fillPrice: number; qtyOpen: number; expiry: string }>

// 2. Sell some or all of an open Nexus trade.
//    qty = a number, or 'all'. Throws on refusal.
exitTrade(tradeId: string, qty: number | 'all'): Promise<void>

// 3. List the account's Nexus trades (open and closed), for the P&L view and to rebuild state.
listTrades(): Promise<Array<{ tradeId: string; contract: Contract; qtyOpen: number; fillPrice: number;
                              status: 'open' | 'closed'; unrealized$: number; realized$: number }>>

// 4. The live mid of one option contract from any options quote source you have.
//    Return null when there is no two-sided quote.
getOptionMid(c: Contract): Promise<number | null>

// 5. The option chain near a price (for picking a contract). Return quoted contracts only.
getChain(root: string, expiryFrom: string, expiryTo: string, strikeLo: number, strikeHi: number):
  Promise<Array<{ contract: Contract; mid: number; delta?: number }>>

// 6. The live price of the underlying.
getPrice(symbol: string): Promise<number | null>

// 7. (optional) Send a chat message. No secrets in the text.
notify(text: string): Promise<void>
```

Rules for the plugs:
- The **tag** you pass to `placeOrder` is the trader's own order label. Make it **letters and digits only, 1 to 64
  characters** (strip `|`, `.`, `-`, spaces). Put a timestamp at the end so it is unique. Some venues refuse any
  other character.
- `placeOrder` sends a **market** order. Do not send a limit price.
- The venue refuses orders outside market hours. Treat that as "no trade", not as an error to retry.

---

## 4. Configuration (the trader's own book)

```ts
const CFG = {
  equity:     100000,   // the simulated book size in $, separate from any other account
  swingPct:   15,       // % of equity per swing trade
  intraPct:   2,        // % of equity per same-day (0DTE) trade
  entryBand:  0.0025,   // 0.25% — how close price must be to the entry
  stop:      -0.80,     // close all at −80% of the premium
  trim:       0.20,     // sell half at +20% of the premium
  trimFrac:   0.5,
  out:        0.30,     // close the rest at +30% of the premium
  tickMs:     20000,    // exit check every 20 s
  lastIntraEntryET: '15:30',  // no new 0DTE entry after this time
};
```

### 4.1 Size

```ts
function size(mid: number, pct: number): number {
  const budget = CFG.equity * pct / 100;       // dollars for this trade
  const perContract = mid * 100;               // dollars for one contract
  if (!(perContract > 0)) return 0;            // no price → no trade
  return Math.max(1, Math.floor(budget / perContract));   // at least 1 contract
}
```

Example: equity $100,000, swing 15% → $15,000. Mid $2.50 → $250 a contract → 60 contracts.

---

## 5. Entry logic

### 5.1 Swing lane (a signal that can last days)

Check every live tick, during market hours only:

```
for each signal in the signal list:
    skip if the signal is marked no-trade or dead
    skip if the signal has no side (up/down) or no entry price
    price = getPrice(signal.symbol)
    skip if |price − entry| / entry > CFG.entryBand          # not at the level yet
    skip if this signal was already tried today              # ONE try per signal, whatever the answer
    mark it tried
    contract = the signal's chosen contract on ITS OWN side  # up → a call, down → a put
    skip (and log why) if there is no priced contract on that side
    qty = size(contract.mid, CFG.swingPct)
    enter(contract, qty, lane = 'swing')
```

How to choose the contract if the signal does not name one: from `getChain`, take the nearest expiry that has at
least a few days left, and the strike nearest the entry price on the signal's side, with a two-sided quote. Never
take the other side of the signal.

### 5.2 Intraday lane (same-day 0DTE)

When the intraday signal says GO for a name:

```
skip if now ≥ CFG.lastIntraEntryET
skip if this (day, name, entry) was already tried
contract = today's expiry, strike nearest the entry, call if up / put if down, must have a quote
skip (and log why) if none
qty = size(contract.mid, CFG.intraPct)
enter(contract, qty, lane = 'intra')
```

Index names trade as their own index options (for example SPXW, NDXP), not as an ETF.

### 5.3 enter()

```
enter(contract, qty, lane):
    refuse if an open position already exists for this underlying in this lane   # one per name per lane
    refuse if the board is a history / replay board                              # never trade the past
    tag = lettersAndDigitsOnly('sfx' + lane + root + strike) + timestampMs, last 64 chars
    try:
        t = placeOrder(contract, qty, tag)
    except refusal as e:
        log "entry refused — " + e.message ; return
    position = { id: t.tradeId, lane, contract, qty, remaining: t.qtyOpen,
                 entry: t.fillPrice, trimmed: false, status: 'open', exits: [] }
    save position (local storage / file / db — it is state, not a secret)
    if t.expiry ≠ contract.expiry:                 # the venue filled a different expiry
        exitTrade(t.tradeId, 'all') ; log "wrong expiry — closed at once" ; return
    notify("NEXUS FILL · qty × contract @ fillPrice — option · stop −80% · half +20% · out +30%")
```

---

## 6. Exit logic (the ladder, on the option premium)

Run every `CFG.tickMs` during market hours, for **every** tracked open position, even if new entries are switched
off.

```ts
function decide(p, mid) {
  if (p.status !== 'open' || !(p.entry > 0) || !(mid > 0) || !(p.remaining > 0)) return null;
  const r = mid / p.entry - 1, eps = 1e-9;          // eps: 2.40/2.00 − 1 = 0.19999… must count as +20%
  if (r <= CFG.stop + eps) return { act: 'stop', qty: p.remaining };
  if (r >= CFG.out  - eps) return { act: 'out',  qty: p.remaining };
  if (r >= CFG.trim - eps && !p.trimmed && p.remaining >= 2)
    return { act: 'trim', qty: Math.floor(p.remaining * CFG.trimFrac) };
  return null;
}
```

Order of the checks matters: stop first, then the full exit, then the trim. A position with 1 contract is never
trimmed; it waits for +30% or −80%.

```
tick():
    if a tick is still running: return            # never two at once
    for each open position p:
        mid = getOptionMid(p.contract)
        d = decide(p, mid) ; if none: continue
        try:
            exitTrade(p.id, d.act == 'trim' ? d.qty : 'all')
            p.remaining −= qty sold ; if d.act == 'trim': p.trimmed = true
            if p.remaining == 0: p.status = 'closed'
            save ; log and notify "<act>: sold qty × contract · N left / flat"
        except refusal as e:
            log "exit refused — " + e.message      # try again on the next tick, not in a loop
```

Worked example: fill 10 contracts at $2.00.
- Mid $2.40 → r = +20% → sell 5 (trim). 5 left.
- Mid $2.60 → r = +30% → sell 5 (out). Flat.
- Or: mid $0.40 → r = −80% → sell all 10 (stop).

---

## 7. Guards (all MUST hold)

1. Options only. Buy to open, sell to close. Never short. Never shares.
2. Market hours only, for entries and exits.
3. One open position per underlying per lane.
4. One entry try per signal per day. A refusal is final for that signal.
5. Never trade a history or replay signal.
6. Wrong expiry filled → close it at once.
7. Exits run for every tracked position even when entries are off.
8. Sizes never go below 1 contract; a missing price means no trade.
9. No key, token or password in any log, message, file or export.
10. The trader never changes the signal's side.

---

## 8. Logs

Keep two append-only logs (one line per event, no secrets):

- **orders**: time, action (enter / exit), contract, qty, tag, answer (ok + trade id + fill price, or the refusal
  text).
- **outbox**: time, channel, message type, first line of the message.

Rebuild open positions at start-up from your saved state, then check them against `listTrades()`.

---

## 9. Tests to write before you switch it on

| test | input | expected |
|---|---|---|
| size | equity 100k, 15%, mid 2.50 | 60 |
| size floor | equity 100k, 2%, mid 30.00 | 1 |
| no price | mid 0 | 0, no order |
| trim at the edge | entry 2.00, mid 2.40, 10 left | trim 5 |
| no trim on 1 | entry 2.00, mid 2.40, 1 left | null |
| out | entry 2.00, mid 2.60 | out, all |
| stop | entry 2.00, mid 0.40 | stop, all |
| inside the band | stop not hit, r = +10% | null |
| tag | key `2026-10-07\|intra\|QQQ\|752.0` | letters/digits only, ≤ 64 |
| wrong side | signal up, contract a put | refused |
| second position | open QQQ swing, new QQQ swing signal | refused |
| late 0DTE | intraday GO at 15:31 ET | no entry |
| wrong expiry | fill expiry ≠ order expiry | exitTrade(id, 'all') sent |

Each test must also have a control: break the rule on purpose and check that the test fails.

---

## 10. What to expect (honest numbers)

- Nexus is simulated. Its P&L moves a public profile, not money.
- In this desk's studies, **every fixed premium exit rule lost money** compared with holding (options pay from a few
  big winners; cutting them cuts the edge). The −80% / +20% / +30% ladder is a user choice, not a tested edge.
- Fill precision matters more than anything else: a 0.25% worse entry removed about a quarter of the edge in tests.
  That is why the entry waits for the level and never chases.
