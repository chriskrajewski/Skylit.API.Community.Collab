import React from 'react';
import { Badge } from '../data/Badge.jsx';
import { Icon } from '../brand/Icon.jsx';
import { LevelLadder } from '../data/LevelLadder.jsx';

const GRADE = { A_Plus: 'A+', Alert_2R: '2R', Pass: 'PASS' };
const LV = { king: 'var(--level-king)', ceiling: 'var(--level-ceiling)', floor: 'var(--level-floor)', gatekeeper: 'var(--level-gatekeeper)', wall: 'var(--level-wall)', air: 'var(--level-air)' };
const fx = (n, d = 2) => n == null || Number.isNaN(Number(n)) ? '—' : Number(n).toFixed(d);

function useWidth() {
  const ref = React.useRef(null), [w, setW] = React.useState(640);
  React.useLayoutEffect(() => {
    if (!ref.current) return;
    const ro = new ResizeObserver(e => setW(Math.max(320, Math.round(e[0].contentRect.width))));
    ro.observe(ref.current); return () => ro.disconnect();
  }, []);
  return [ref, w];
}

function TradeChart({ t }) {
  const [ref, W] = useWidth();
  const H = 372, G = 156, PW = W - G, short = t.direction === 'short';
  const bars = t.bars || [], tg = t.targets || [];
  const plan = [t.entry, t.stop, t.spot, ...tg.map(x => x.price)].filter(n => n != null && !Number.isNaN(Number(n)));
  const lo0 = Math.min(...plan, ...(bars.length ? bars.map(b => b.l) : plan));
  const hi0 = Math.max(...plan, ...(bars.length ? bars.map(b => b.h) : plan));
  const pad = Math.max((hi0 - lo0) * 0.12, 0.25), lo = lo0 - pad, hi = hi0 + pad;
  const inRange = l => (l.to != null ? Math.max(l.price, l.to) >= lo && Math.min(l.price, l.to) <= hi : l.price >= lo && l.price <= hi);
  const lvls = (t.levels || []).filter(inRange), off = (t.levels || []).filter(l => !inRange(l));
  const y = p => 14 + (hi - p) / (hi - lo) * (H - 28);
  const split = PW * 0.5, cw = split / Math.max(bars.length, 1);
  const near = p => plan.some(q => Math.abs(q - p) < (hi - lo) * 0.012);
  const xs = [split, PW * 0.62, PW * 0.74, PW * 0.86];
  const pts = [[xs[0], t.spot], [xs[1], t.entry], ...tg.map((x, i) => [xs[2 + i] ?? PW * 0.9, x.price])];
  const pct = p => ((p - t.spot) / t.spot * 100);
  const gut = [
    { k: 'stop', label: 'STOP', p: t.stop, c: 'var(--color-bear)' },
    { k: 'entry', label: 'ENTRY', p: t.entry, c: 'var(--color-ice)' },
    { k: 'now', label: 'NOW', p: t.spot, c: 'var(--text-primary)' },
    ...tg.map(x => ({ k: x.label, label: x.label, p: x.price, c: 'var(--color-bull)' }))
  ].map(g => ({ ...g, y: y(g.p) })).sort((a, b) => a.y - b.y);
  for (let i = 1; i < gut.length; i++) if (gut[i].y - gut[i - 1].y < 26) gut[i].y = gut[i - 1].y + 26;
  const lines = [
    { p: t.stop, c: 'var(--color-bear)', dash: '5 4', label: 'STOP' },
    { p: t.entry, c: 'var(--color-ice)', label: 'ENTRY' },
    ...tg.map(x => ({ p: x.price, c: 'var(--color-bull)', label: x.label }))
  ];
  return (
    <div ref={ref} className="fse-tcard__chart">
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`${t.instrument} ${t.direction} plan chart`}>
        <defs><linearGradient id="fse-tc-proj" x1="0" x2="1"><stop offset="0" stopColor="var(--color-ice)" stopOpacity="0" /><stop offset="1" stopColor="var(--color-ice)" stopOpacity=".05" /></linearGradient></defs>
        <rect x={split} y="0" width={PW - split} height={H} fill="url(#fse-tc-proj)" />
        <line x1={split} x2={split} y1="0" y2={H} stroke="var(--border-default)" strokeDasharray="2 3" />
        <text x={split + 6} y={H - 6} className="fse-tcard__ax">projected · ① fill → ② {tg[0] ? tg[0].label : ''}{tg[1] ? ' → ③ ' + tg[1].label : ''}</text>
        <text x={4} y={H - 6} className="fse-tcard__ax">{bars.length ? `last ${bars.length} × 1-min` : 'bars unavailable'}</text>
        {lvls.map((l, i) => l.to != null
          ? <g key={i}><rect x="0" width={PW} y={y(Math.max(l.price, l.to))} height={Math.abs(y(l.price) - y(l.to))} fill="var(--text-primary)" opacity=".035" /><text x={4} y={y(Math.max(l.price, l.to)) + 11} className="fse-tcard__lv" fill="var(--text-tertiary)">{(l.label || 'AIR POCKET').toUpperCase()} {fx(l.price, 0)}–{fx(l.to, 0)}</text></g>
          : <g key={i}>{!near(l.price) && <line x1="0" x2={PW} y1={y(l.price)} y2={y(l.price)} stroke={LV[l.kind] || 'var(--text-tertiary)'} strokeOpacity=".45" strokeDasharray={l.kind === 'gatekeeper' ? '1 3' : '3 4'} />}<text x={4} y={y(l.price) - 4} className="fse-tcard__lv" fill={LV[l.kind] || 'var(--text-tertiary)'}>{(l.label || l.kind).toUpperCase()} {fx(l.price, 0)}</text></g>)}
        {bars.map((b, i) => {
          const up = b.c >= b.o, x = i * cw + cw / 2, bw = Math.max(2, cw * 0.6);
          return <g key={i}><line x1={x} x2={x} y1={y(b.h)} y2={y(b.l)} stroke={up ? 'var(--color-candle-up-wick)' : 'var(--color-candle-down-wick)'} strokeOpacity={up ? 1 : .55} /><rect x={x - bw / 2} y={y(Math.max(b.o, b.c))} width={bw} height={Math.max(1, Math.abs(y(b.o) - y(b.c)))} fill={up ? 'var(--color-candle-up-body)' : 'var(--color-candle-down-body)'} stroke={up ? 'var(--color-candle-up-border)' : 'none'} strokeWidth=".5" /></g>;
        })}
        {lines.map((l, i) => <g key={i}><line x1={i === 0 ? 0 : split} x2={PW} y1={y(l.p)} y2={y(l.p)} stroke={l.c} strokeWidth={l.dash ? 1 : 1.25} strokeDasharray={l.dash} /><text x={PW - 4} y={y(l.p) - 5} textAnchor="end" className="fse-tcard__pl" fill={l.c}>{l.label}</text></g>)}
        <line x1="0" x2={PW} y1={y(t.spot)} y2={y(t.spot)} stroke="var(--text-primary)" strokeOpacity=".5" strokeWidth=".75" />
        <polyline points={pts.slice(0, 2).map(p => `${p[0]},${y(p[1])}`).join(' ')} fill="none" stroke="var(--text-secondary)" strokeWidth="1.5" strokeDasharray="4 4" />
        <polyline points={pts.slice(1).map(p => `${p[0]},${y(p[1])}`).join(' ')} fill="none" stroke="var(--color-bull)" strokeWidth="6" strokeOpacity=".12" strokeLinejoin="round" />
        <polyline points={pts.slice(1).map(p => `${p[0]},${y(p[1])}`).join(' ')} fill="none" stroke="var(--color-bull)" strokeWidth="2" strokeLinejoin="round" />
        <circle cx={pts[0][0]} cy={y(t.spot)} r="4" fill="var(--text-primary)" />
        {pts.slice(1).map((p, i) => {
          const c = i === 0 ? 'var(--color-ice)' : 'var(--color-bull)';
          return <g key={i}><circle cx={p[0]} cy={y(p[1])} r="9" fill="var(--surface-panel)" stroke={c} strokeWidth="1.5" /><text x={p[0]} y={y(p[1]) + 3.5} textAnchor="middle" className="fse-tcard__num" fill={c}>{i + 1}</text></g>;
        })}
        <line x1={PW} x2={PW} y1="0" y2={H} stroke="var(--border-subtle)" />
        {gut.map((g, i) => <g key={i}>
          <text x={PW + 10} y={g.y + 4} className="fse-tcard__gp">{g.k === 'now' ? '' : (pct(g.p) >= 0 ? '+' : '') + pct(g.p).toFixed(2) + '%'}</text>
          <text x={W - 2} y={g.y + 5} textAnchor="end" className="fse-tcard__gv" fill={g.c}>{fx(g.p)}</text>
        </g>)}
      </svg>
      {off.length > 0 && <div className="fse-tcard__off">{off.map((l, i) => <span key={i}><b style={{ color: LV[l.kind] }}>{(l.label || l.kind)}</b> {fx(l.price)} {l.price > hi ? '↑' : '↓'}</span>)}<span>off scale</span></div>}
    </div>
  );
}

function Ring({ pct, label, color }) {
  const r = 30, C = 2 * Math.PI * r;
  return (
    <div className="fse-tcard__ring">
      <svg width="76" height="76" viewBox="0 0 76 76"><circle cx="38" cy="38" r={r} fill="none" stroke="var(--surface-active)" strokeWidth="6" /><circle cx="38" cy="38" r={r} fill="none" stroke={color} strokeWidth="6" strokeLinecap="round" strokeDasharray={`${C * pct / 100} ${C}`} transform="rotate(-90 38 38)" /><text x="38" y="43" textAnchor="middle" className="fse-tcard__ringv">{pct}%</text></svg>
      <span>{label}</span>
    </div>
  );
}

function setupWhy(s) {
  if (s.why && s.why.length) return s.why.join(' · ');
  if (s.reasons && s.reasons.length) return s.reasons.join(' · ');
  return null;
}

function whyHeading(t) {
  const label = (t.actionLabel || '').toLowerCase();
  if (label === 'skip' || /^skip\b/i.test(t.action || '')) return 'Why this skipped';
  if (label === 'hold') return 'Why this held';
  if (t.grade === 'Pass') return 'Why this passed';
  if (label === 'exit' || label === 'result') return 'Why this result';
  if (label === 'info') return 'Why this note';
  return 'Why this decision';
}

function passBanner(t) {
  const label = (t.actionLabel || '').toLowerCase();
  if (label === 'skip' || /^skip\b/i.test(t.action || '')) return 'Skip — no order armed';
  if (label === 'hold') return 'Hold — no order armed';
  if (label === 'info') return 'Info — no order armed';
  return 'Pass — no order armed';
}

function RichCard({ c, className }) {
  const t = c.trade, short = t.direction === 'short', dirC = short ? 'var(--color-bear)' : 'var(--color-bull)';
  const isPass = t.grade === 'Pass';
  const risk = Math.abs(t.entry - t.stop) || 1, ticks = p => Math.round(Math.abs(p - t.entry) / (t.tick || 0.25));
  const usd = (p, q) => ticks(p) * (t.tickValue || 1.25) * q;
  const others = (c.setups || []).slice(1);
  const oddsC = ['var(--color-ice)', 'var(--color-bull)', 'var(--color-bull)'];
  const dayColor = t.dayPct == null ? undefined : t.dayPct < 0 ? 'var(--color-bear)' : 'var(--color-bull)';
  const why = c.whyPassed || setupWhy(c.setups?.[0]);
  const ladder = c.ladder || [];
  return (
    <article className={'fse-fcard fse-tcard' + (isPass ? ' fse-tcard--pass' : '') + (className ? ' ' + className : '')}>
      <header className="fse-tcard__head">
        <div className="fse-tcard__tile" data-grade={t.grade}>{GRADE[t.grade] || t.grade}</div>
        <div className="fse-tcard__id">
          <div className="fse-tcard__sym">{t.instrument}<span style={{ color: dirC }}>{t.direction.toUpperCase()}</span></div>
          <div className="fse-tcard__sub">{t.name} · {t.pattern} · {t.node} · tap {t.tap} · {c.time}</div>
        </div>
        <div className="fse-tcard__pill" style={{ '--pc': isPass ? 'var(--text-secondary)' : dirC }}>{t.direction.toUpperCase()} · {GRADE[t.grade]} · {fx(t.rr, 1)}R</div>
        {c.mode && <Badge mode={c.mode} />}
      </header>
      {isPass && <div className="fse-tcard__passban" role="status">{passBanner(t)}</div>}
      <div className="fse-tcard__stats">
        <div><span>Spot</span><b>{fx(t.spot)}</b></div>
        <div><span>Day</span><b style={{ color: dayColor }}>{t.dayPct == null ? '—' : `${t.dayPct > 0 ? '+' : ''}${fx(t.dayPct)}%`}</b></div>
        <div><span>Range</span><b>{t.rangePct == null ? '—' : `${fx(t.rangePct)}%`}</b></div>
        <div><span>Regime</span><b>{c.regime ?? '—'}</b></div>
        <div><span>Map</span><b>{c.mapGrade ?? '—'}</b></div>
        <div><span>Trinity</span><b style={{ color: c.trinity === 'agree' ? 'var(--color-bull)' : undefined }}>{c.trinity || '—'}</b></div>
        <div><span>Gates</span><b>{t.gates || '—'}</b></div>
      </div>
      <div className="fse-tcard__grid">
        <section className="fse-tcard__panel fse-tcard__panel--chart">
          <h4 className="fse-tcard__h">Trade · to scale</h4>
          <TradeChart t={t} />
        </section>
        <div className="fse-tcard__col">
          <section className="fse-tcard__panel">
            <h4 className="fse-tcard__h">The plan</h4>
            <div className="fse-tcard__plan">
              <span className="fse-tcard__tag" data-tone="ice">IF</span><span>{t.plan.if}</span>
              <span className="fse-tcard__tag" data-tone="bear">OUT</span><span>{t.plan.out}</span>
              <span className="fse-tcard__tag" data-tone="bull">TGT</span><span>{t.plan.tgt}</span>
            </div>
            {t.context && t.context.length > 0 && <div className="fse-tcard__ctx">{t.context.map((x, i) => <div key={i}><Icon name={x.icon} size={13} style={{ color: x.icon === 'crown' ? 'var(--level-king)' : 'var(--text-tertiary)' }} /><span>{x.text}</span></div>)}</div>}
          </section>
          <section className="fse-tcard__panel">
            <h4 className="fse-tcard__h">Backtest odds<span>{t.odds ? t.odds.label : 'odds unavailable'}</span></h4>
            {t.odds && t.odds.items && t.odds.items.length > 0
              ? <div className="fse-tcard__rings">{t.odds.items.map((o, i) => <Ring key={i} pct={o.pct} label={o.label} color={oddsC[i] || 'var(--color-bull)'} />)}</div>
              : <div className="fse-tcard__unavailable">Odds unavailable for this pattern</div>}
          </section>
          {why && (
            <section className="fse-tcard__panel fse-tcard__panel--why">
              <h4 className="fse-tcard__h">{whyHeading(t)}</h4>
              <p className="fse-tcard__whypass">{why}</p>
            </section>
          )}
        </div>
      </div>
      {t.action && <div className={'fse-tcard__do' + (isPass ? ' fse-tcard__do--pass' : '')}><span className="fse-tcard__dotag">{t.actionLabel || (isPass ? 'Pass' : 'Armed')}</span><span className="fse-tcard__dotext">{t.action}</span>{c.mode === 'paper' && <span className="fse-tcard__dometa">paper · no live money</span>}</div>}
      <section className="fse-tcard__panel">
        <h4 className="fse-tcard__h">Bracket · {t.qty} {t.instrument}<span>${fx(t.tickValue)} / tick · stop only tightens</span></h4>
        <div className="fse-tcard__table" role="table">
          <div className="th" role="row"><span>Leg</span><span>Order</span><span>Price</span><span>Ticks</span><span>Qty</span><span>P&amp;L</span><span>R</span></div>
          <div role="row"><span style={{ color: 'var(--color-ice)' }}>Entry</span><span>{short ? 'sell' : 'buy'} limit</span><span className="v">{fx(t.entry)}</span><span>—</span><span>×{t.qty}</span><span>—</span><span>—</span></div>
          <div role="row"><span style={{ color: 'var(--color-bear)' }}>Stop</span><span>{short ? 'buy' : 'sell'} stop</span><span className="v" style={{ color: 'var(--color-bear)' }}>{fx(t.stop)}</span><span>{ticks(t.stop)}</span><span>×{t.qty}</span><span style={{ color: 'var(--color-bear)' }}>−${fx(usd(t.stop, t.qty))}</span><span style={{ color: 'var(--color-bear)' }}>−1.00</span></div>
          {(t.targets || []).map((x, i) => <div key={i} role="row"><span style={{ color: 'var(--color-bull)' }}>{x.label}</span><span>{short ? 'buy' : 'sell'} limit</span><span className="v" style={{ color: 'var(--color-bull)' }}>{fx(x.price)}</span><span>{ticks(x.price)}</span><span>×{x.qty}</span><span style={{ color: 'var(--color-bull)' }}>+${fx(usd(x.price, x.qty))}</span><span style={{ color: 'var(--color-bull)' }}>+{fx(Math.abs(x.price - t.entry) / risk)}</span></div>)}
        </div>
      </section>
      <section className="fse-tcard__panel fse-tcard__panel--map">
        <h4 className="fse-tcard__h">Map{c.flip && <span>King flip {c.flip}</span>}</h4>
        <div className="fse-tcard__maps">{(c.maps || []).map((m, i) => <div key={i} className="fse-tcard__map">
          <span className="s">{m.sym}</span>
          <span><i>K</i><b style={{ color: 'var(--level-king)' }}>{m.king ?? '—'}</b></span>
          <span><i>C</i><b style={{ color: 'var(--level-ceiling)' }}>{m.ceiling ?? '—'}</b></span>
          <span><i>F</i><b style={{ color: 'var(--level-floor)' }}>{m.floor ?? '—'}</b></span>
          <span><i>spot</i><b>{m.spot ?? '—'}</b></span>
        </div>)}</div>
        {ladder.length > 0
          ? <LevelLadder levels={ladder} spot={c.ladderSpot ?? t.spot} className="fse-tcard__ladder" />
          : <div className="fse-tcard__unavailable">GEX heatmap unavailable</div>}
      </section>
      {others.length > 0 && <section className="fse-tcard__panel">
        <h4 className="fse-tcard__h">Other setups{c.notListed > 0 && <span>+{c.notListed} not listed · watch {c.watchUp} ↑ · {c.watchDown} ↓</span>}</h4>
        {others.map((s, i) => {
          const why = setupWhy(s);
          return <div key={i} className="fse-fcard__setup"><Badge grade={s.grade} /><span className="t">{s.key}</span>{why && <span className="fse-tcard__why">{why}</span>}</div>;
        })}
      </section>}
    </article>
  );
}

export function FindingCard({ card = {}, className }) {
  const c = card, ua = v => v == null ? <span style={{ color: 'var(--text-disabled)' }}>unavailable</span> : v;
  if (c.trade && c.kind !== 'premarket') return <RichCard c={c} className={className} />;
  return (
    <article className={'fse-fcard' + (className ? ' ' + className : '')}>
      <header className="fse-fcard__head">
        <Icon name={c.kind === 'premarket' ? 'sunrise' : 'radar'} size={14} style={{ color: 'var(--text-tertiary)' }} />
        <span className="fse-fcard__time">{c.kind === 'premarket' ? 'Premarket' : 'Decision'} · {c.time}</span>
        {c.mode && <Badge mode={c.mode} />}
      </header>
      {c.summary && <div className="fse-fcard__summary">{c.summary}</div>}
      <div className="fse-fcard__sec">
        <span className="fse-fcard__h">Map</span>
        <div className="fse-fcard__row"><span className="l">Regime</span><span>{ua(c.regime)}</span><span style={{ color: 'var(--text-tertiary)' }}>grade</span><span>{ua(c.mapGrade)}</span><span style={{ color: 'var(--text-tertiary)' }}>trinity</span><span>{ua(c.trinity)}</span></div>
        {(c.maps || []).map((m, i) => <div key={i} className="fse-fcard__row"><span className="l">{m.sym}</span><span>K <b style={{ color: 'var(--level-king)', fontWeight: 500 }}>{ua(m.king)}</b></span><span>C {ua(m.ceiling)}</span><span>F {ua(m.floor)}</span><span style={{ color: 'var(--text-tertiary)' }}>spot {ua(m.spot)}</span></div>)}
        {c.flip && <div className="fse-fcard__row"><span className="l">Flip</span><span>King {c.flip}</span></div>}
      </div>
      {c.kind !== 'premarket' && <div className="fse-fcard__sec">
        <span className="fse-fcard__h">Setups</span>
        {(c.setups || []).length === 0 && <span style={{ color: 'var(--text-disabled)' }}>none</span>}
        {(c.setups || []).map((s, i) => {
          const why = setupWhy(s);
          return <React.Fragment key={i}>
            <div className="fse-fcard__setup"><Badge grade={s.grade} /><span className="t">{s.key}</span></div>
            {why && <div className="fse-fcard__reasons">{why}</div>}
          </React.Fragment>;
        })}
        {c.notListed > 0 && <span style={{ color: 'var(--text-tertiary)', fontSize: 11 }}>+{c.notListed} not listed</span>}
      </div>}
      {c.kind !== 'premarket' && <div className="fse-fcard__sec">
        <span className="fse-fcard__h">Orders · positions</span>
        <div className="fse-fcard__row"><span className="l">Work</span><span>{c.orders || <span style={{ color: 'var(--text-disabled)' }}>none</span>}</span></div>
        <div className="fse-fcard__row"><span className="l">Pos</span><span className={/^(long|buy)/i.test(String(c.position || '')) ? 'is-long' : /^(short|sell)/i.test(String(c.position || '')) ? 'is-short' : undefined}>{c.position || 'flat'}</span></div>
        <div className="fse-fcard__row"><span className="l">Watch</span><span>{ua(c.watchUp)} ↑ · {ua(c.watchDown)} ↓</span></div>
      </div>}
    </article>
  );
}
