/*
 * VEX STAIR-STEP DETECTOR — for Meta Muse (plain JavaScript, no dependencies)
 * ===========================================================================
 *
 * INSTRUCTIONS FOR THE BOT
 * Use this to tell whether a ticker's VEX (dealer vanna exposure) book forms a STAIR-STEP UP, a
 * STAIR-STEP DOWN, or NO STAIR across its option expiries.
 *   1. book = await loadVannaBook(symbol)   — returns { spot, session, expirations, strikes, vanna }
 *        spot         underlying price the book was built at
 *        session      trading date of the book, 'YYYY-MM-DD'
 *        expirations  expiry dates, nearest first, 'YYYY-MM-DD'
 *        strikes      strike prices, ascending
 *        vanna        vanna[i][j] = dollar vanna at strikes[i] for expirations[j] (per expiry, not summed)
 *   2. result = detectVexStair(book)
 *   3. Answer with the direction (UP / DOWN / NONE), every step as "strike (expiry)" near → far, and the
 *      caveat. Never turn the stair into a buy or sell call.
 *
 * THE RULE
 *   - Strikes within ±50% of spot; expiries 2–120 calendar days after the session.
 *   - Vanna KING = the largest |vanna| cell in that window.
 *   - MAJOR expiry = its own biggest |vanna| cell is ≥ 25% of the King (thin expiries are dropped).
 *   - STEP = the strike of each major expiry's dominant node, nearest expiry first.
 *   - UP = Spearman ρ(expiry order, step strike) ≥ +0.6 AND last step ≥ 3% of spot above the first;
 *     DOWN mirrors it; fewer than 3 major expiries = no read.
 *   - Read the dominant POSITIVE node first, then the dominant node of EITHER sign; disagreement → NONE.
 *
 * CAVEAT
 *   Detection is reliable; prediction is not. 253 names, Jan–Sep 2026 (~2,700 stairs): price went the
 *   stair's way (vs the day's average) 49.3% over 5 sessions and 48.0% over 10 — a coin flip. A stair
 *   shows where positioning has been, not where price goes; most dissolve within a week.
 */
'use strict';

const RULE = { window: 0.5, dteMin: 2, dteMax: 120, majorShare: 0.25, rho: 0.6, net: 0.03, minSteps: 3 };

/** PLUG YOUR DATA SOURCE IN HERE — return { spot, session, expirations, strikes, vanna } for `symbol`. */
async function loadVannaBook(symbol) {
  throw new Error('connect loadVannaBook() to your own data source');
}

function daysBetween(a, b) { return Math.round((Date.parse(b + 'T00:00:00Z') - Date.parse(a + 'T00:00:00Z')) / 864e5); }

function spearman(x, y) {
  const n = x.length;
  const ranks = (a) => { const s = a.map((v, i) => [v, i]).sort((p, q) => p[0] - q[0]); const r = new Array(n);
    for (let i = 0; i < n;) { let j = i; while (j + 1 < n && s[j + 1][0] === s[i][0]) j++; for (let k = i; k <= j; k++) r[s[k][1]] = (i + j) / 2; i = j + 1; } return r; };
  const a = ranks(x), b = ranks(y), ma = a.reduce((s, v) => s + v, 0) / n, mb = b.reduce((s, v) => s + v, 0) / n;
  let num = 0, da = 0, db = 0; for (let i = 0; i < n; i++) { num += (a[i] - ma) * (b[i] - mb); da += (a[i] - ma) ** 2; db += (b[i] - mb) ** 2; }
  return da && db ? num / Math.sqrt(da * db) : 0;
}

function detectVexStair(book) {
  if (!book || !(book.spot > 0) || !book.strikes || !book.strikes.length || !book.expirations || !book.expirations.length) return { direction: 'NONE', read: 'no book' };
  const { spot, strikes, expirations, vanna } = book;
  const cols = expirations.map((e, j) => ({ j, e, dte: daysBetween(book.session, e) })).filter((c) => c.dte >= RULE.dteMin && c.dte <= RULE.dteMax);
  const rows = strikes.map((k, i) => i).filter((i) => Math.abs(strikes[i] / spot - 1) <= RULE.window);
  if (!cols.length || !rows.length) return { direction: 'NONE', read: 'no expiries or strikes in the window' };
  let king = 0; for (const c of cols) for (const i of rows) king = Math.max(king, Math.abs(+vanna[i][c.j] || 0));
  if (!(king > 0)) return { direction: 'NONE', read: 'empty book' };

  const read = (positiveOnly) => {
    const steps = [];
    for (const c of cols) {
      let best = null;
      for (const i of rows) { const v = +vanna[i][c.j] || 0;
        if (positiveOnly ? (v > 0 && (!best || v > best.v)) : (v && (!best || Math.abs(v) > Math.abs(best.v)))) best = { k: strikes[i], v }; }
      if (best && Math.abs(best.v) >= RULE.majorShare * king) steps.push({ strike: best.k, expiry: c.e, dte: c.dte, vanna: best.v, pctOfKing: Math.round(Math.abs(best.v) / king * 100) });
    }
    if (steps.length < RULE.minSteps) return { direction: null, steps, rho: null, netPct: null, tooFew: true };
    const ks = steps.map((s) => s.strike), rho = spearman(ks.map((_, i) => i), ks), net = (ks[ks.length - 1] - ks[0]) / spot;
    const direction = rho >= RULE.rho && net >= RULE.net ? 'UP' : rho <= -RULE.rho && net <= -RULE.net ? 'DOWN' : null;
    return { direction, steps, rho: +rho.toFixed(2), netPct: +(net * 100).toFixed(1), tooFew: false };
  };

  const pos = read(true), any = read(false);
  const conflict = !!(pos.direction && any.direction && pos.direction !== any.direction);
  const main = conflict ? null : pos.direction ? pos : any.direction ? any : null;
  const shown = main || (!pos.tooFew ? pos : any);
  return {
    direction: main ? main.direction : 'NONE',
    read: main ? (main === pos ? 'positive node' : 'either-sign node') : (conflict ? 'conflict' : 'no stair'),
    rho: shown.rho, netPct: shown.netPct, steps: shown.steps,
    path: shown.steps.map((s) => `${s.strike} (${s.expiry})`).join(' → '),
    positiveRead: pos.direction || 'NONE', eitherSignRead: any.direction || 'NONE',
    caveat: 'Descriptive only: a stair did not predict direction (49.3% / 48.0% at 5 / 10 sessions, 253 names, 2026).',
  };
}

async function detectForSymbol(symbol) { return detectVexStair(await loadVannaBook(symbol)); }

if (typeof module !== 'undefined') module.exports = { RULE, loadVannaBook, detectVexStair, detectForSymbol };

// Self-test on an ILLUSTRATIVE book (made-up numbers): the either-sign node climbs 100 → 105 → 110 → 115.
if (typeof require !== 'undefined' && typeof module !== 'undefined' && require.main === module) {
  const demo = { spot: 100, session: '2026-10-01', expirations: ['2026-10-09', '2026-10-16', '2026-11-20', '2026-12-18'],
    strikes: [100, 105, 110, 115], vanna: [[-9e6, 0, 0, 0], [0, -9e6, 0, 0], [0, 0, 9e6, 0], [0, 0, 0, 9e6]] };
  const r = detectVexStair(demo); console.log(r.direction, '|', r.path, '| rho', r.rho, '| net', r.netPct, '%');
}
