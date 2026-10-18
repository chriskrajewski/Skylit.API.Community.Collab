import React from 'react';
const LV = { king: 'var(--level-king)', ceiling: 'var(--level-ceiling)', floor: 'var(--level-floor)', gatekeeper: 'var(--fg-3)', air: 'var(--fg-4)', spot: 'var(--fg-1)' };
export function CandleChart({ candles = [], levels = [], height = 240, axis = true, times, className }) {
  const ref = React.useRef(null); const [w, setW] = React.useState(600);
  React.useEffect(() => { const el = ref.current; if (!el) return; const ro = new ResizeObserver(e => setW(Math.max(120, Math.floor(e[0].contentRect.width)))); ro.observe(el); return () => ro.disconnect(); }, []);
  if (!candles.length) return <div ref={ref} className="fse-candles" style={{ height }} />;
  const ax = axis ? 64 : 0, pad = 8, cw = w - ax, chH = height - (times ? 18 : 0);
  let lo = Math.min(...candles.map(c => c.l)), hi = Math.max(...candles.map(c => c.h));
  levels.forEach(l => { lo = Math.min(lo, l.price); hi = Math.max(hi, l.price); });
  const span = (hi - lo) || 1; lo -= span * .04; hi += span * .04;
  const y = p => pad + (1 - (p - lo) / (hi - lo)) * (chH - pad * 2);
  const tagY = {}; [...levels].map((l, i) => ({ i, yy: y(l.price) })).sort((a, b) => a.yy - b.yy).reduce((prev, t) => { const v = Math.max(t.yy, prev + 15); tagY[t.i] = v; return v; }, -Infinity);
  const step = cw / candles.length, bw = Math.max(1, Math.min(9, step * .62));
  const ticks = []; const raw = (hi - lo) / 5, mag = Math.pow(10, Math.floor(Math.log10(raw))), st = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => s >= raw) || raw;
  for (let t = Math.ceil(lo / st) * st; t <= hi; t += st) ticks.push(t);
  return (
    <div ref={ref} className={'fse-candles' + (className ? ' ' + className : '')}>
      <svg width={w} height={height}>
        {ticks.map(t => <g key={t}><line className="fse-candles__grid" x1="0" x2={cw} y1={y(t)} y2={y(t)} />{axis && <text className="fse-candles__axis" x={cw + 8} y={y(t) + 3}>{t.toFixed(2)}</text>}</g>)}
        {levels.map((l, i) => { const c = LV[l.kind] || 'var(--fg-2)'; const yy = y(l.price);
          return <g key={'l' + i}><line x1="0" x2={cw} y1={yy} y2={yy} stroke={c} strokeWidth={l.kind === 'king' ? 1.25 : 1} strokeDasharray={l.kind === 'king' || l.kind === 'spot' ? undefined : l.kind === 'air' ? '1 3' : '4 3'} opacity={l.kind === 'gatekeeper' || l.kind === 'air' ? .8 : 1} />
            <text x={4} y={yy - 4} fill={c} style={{ fontWeight: l.kind === 'king' ? 600 : 400, textTransform: 'uppercase', letterSpacing: '.06em' }}>{l.label}</text>
            {axis && <g><rect x={cw + 2} y={tagY[i] - 7} width={ax - 4} height={14} rx="2" fill={l.kind === 'king' ? 'var(--level-king)' : l.kind === 'spot' ? 'var(--fg-1)' : 'var(--surface-active)'} /><text x={cw + 8} y={tagY[i] + 3} fill={l.kind === 'king' || l.kind === 'spot' ? 'var(--ink-0)' : 'var(--fg-1)'} style={{ fontWeight: 600 }}>{l.price.toFixed(2)}</text></g>}</g>; })}
        {candles.map((c, i) => { const up = c.c >= c.o, x = i * step + step / 2, top = y(Math.max(c.o, c.c)), bot = y(Math.min(c.o, c.c));
          return <g key={i}><line x1={x} x2={x} y1={y(c.h)} y2={y(c.l)} stroke={up ? 'var(--color-candle-up-wick)' : 'var(--color-candle-down-wick)'} strokeWidth="1" />
            <rect x={x - bw / 2} y={top} width={bw} height={Math.max(1, bot - top)} fill={up ? 'var(--color-candle-up-body)' : 'var(--color-candle-down-body)'} stroke={up ? 'var(--color-candle-up-border)' : 'var(--color-candle-down-border)'} strokeWidth=".75" /></g>; })}
        {times && times.map((t, i) => t && <text key={'t' + i} className="fse-candles__axis" x={i * step + step / 2} y={height - 4} textAnchor="middle">{t}</text>)}
      </svg>
    </div>
  );
}