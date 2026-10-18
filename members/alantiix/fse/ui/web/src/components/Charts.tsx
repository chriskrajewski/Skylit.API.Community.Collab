import { useRef, useState } from 'react';
import type { Equity } from '../lib/types';
import { DASH, etDateTime, fromNs, signedR, usd } from '../lib/format';

const W = 800;
const PAD = { l: 56, r: 8, t: 8, b: 20 };

function scale(values: number[], h: number) {
  const min = Math.min(0, ...values);
  const max = Math.max(0, ...values);
  const span = max - min || 1;
  return (v: number) => PAD.t + (h - PAD.t - PAD.b) * (1 - (v - min) / span);
}

/** Cumulative equity with drawdown shading and a crosshair (DS chart rules: ice, 1.25 px). */
export function EquityChart({ equity, unit, height = 240 }: {
  equity: Equity; unit: 'r' | 'usd'; height?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const ref = useRef<SVGSVGElement>(null);
  const pts = equity.points;
  if (pts.length === 0) return <div className="empty">No closed trades.</div>;
  const cum = pts.map((p) => Number(unit === 'r' ? p.cum_r : p.cum_usd));
  const dd = pts.map((p) => Number(unit === 'r' ? p.dd_r : p.dd_usd));
  const y = scale([...cum, ...dd], height);
  const x = (i: number) => PAD.l + (pts.length === 1 ? 0 : (i / (pts.length - 1)) * (W - PAD.l - PAD.r));
  const line = cum.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ');
  const ddPath = `M${x(0)} ${y(0)} ` + dd.map((v, i) => `L${x(i).toFixed(1)} ${y(v).toFixed(1)}`).join(' ')
    + ` L${x(pts.length - 1)} ${y(0)} Z`;
  const ticks = [Math.min(0, ...cum, ...dd), 0, Math.max(0, ...cum)];
  const fmt = (v: number) => (unit === 'r' ? signedR(v) : usd(v));
  const onMove = (e: React.MouseEvent) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    const px = ((e.clientX - r.left) / r.width) * W;
    const i = Math.round(((px - PAD.l) / (W - PAD.l - PAD.r)) * (pts.length - 1));
    setHover(Math.max(0, Math.min(pts.length - 1, i)));
  };
  const h = hover === null ? null : pts[hover];
  return (
    <div className="chart">
      <svg ref={ref} viewBox={`0 0 ${W} ${height}`} height={height} onMouseMove={onMove}
        onMouseLeave={() => setHover(null)} role="img" aria-label={`Equity curve in ${unit === 'r' ? 'R' : 'dollars'}`}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line className={t === 0 ? 'chart__zero' : 'chart__grid'} x1={PAD.l} x2={W - PAD.r} y1={y(t)} y2={y(t)} />
            <text className="chart__axis" x={PAD.l - 6} y={y(t) + 3} textAnchor="end">{fmt(t)}</text>
          </g>
        ))}
        <path className="chart__dd" d={ddPath} />
        <path className="chart__area" d={`${line} L${x(pts.length - 1)} ${y(0)} L${x(0)} ${y(0)} Z`} />
        <path className="chart__line" d={line} />
        <text className="chart__axis" x={PAD.l} y={height - 4}>{pts[0].session}</text>
        <text className="chart__axis" x={W - PAD.r} y={height - 4} textAnchor="end">{pts[pts.length - 1].session}</text>
        {hover !== null && (
          <line className="chart__cross" x1={x(hover)} x2={x(hover)} y1={PAD.t} y2={height - PAD.b} />
        )}
      </svg>
      {h && (
        <div className="chart__tip" style={{ left: `calc(${((x(hover!) / W) * 100).toFixed(1)}% + var(--sp-4))` }}>
          <div>{etDateTime(fromNs(h.t_ns))} · {h.instrument ?? DASH}</div>
          <div>trade {signedR(h.r)} · {usd(h.net)}</div>
          <div>cumulative {unit === 'r' ? signedR(h.cum_r) : usd(h.cum_usd)}</div>
          <div className="text-bear">drawdown {unit === 'r' ? signedR(h.dd_r) : usd(h.dd_usd)}</div>
        </div>
      )}
    </div>
  );
}

/** R-multiple distribution: losses bear, wins ice. */
export function Histogram({ bins, height = 140 }: {
  bins: { from: string; to: string; count: number }[]; height?: number;
}) {
  if (bins.length === 0) return <div className="empty">No closed trades.</div>;
  const max = Math.max(...bins.map((b) => b.count), 1);
  const bw = (W - PAD.l - PAD.r) / bins.length;
  return (
    <div className="chart">
      <svg viewBox={`0 0 ${W} ${height}`} height={height} role="img" aria-label="R-multiple distribution">
        {bins.map((b, i) => {
          const h = ((height - PAD.t - PAD.b) * b.count) / max;
          const from = Number(b.from);
          const cls = from < 0 ? 'hist__bar hist__bar--neg' : from > 0 ? 'hist__bar hist__bar--pos' : 'hist__bar';
          return (
            <g key={i}>
              <rect className={cls} x={PAD.l + i * bw + 1} width={Math.max(1, bw - 2)}
                y={height - PAD.b - h} height={h}><title>{`${b.from} to ${b.to} R: ${b.count}`}</title></rect>
              {(bins.length <= 16 || i % Math.ceil(bins.length / 16) === 0) && (
                <text className="chart__axis" x={PAD.l + i * bw + bw / 2} y={height - 6} textAnchor="middle">
                  {signedR(b.from).replace(' R', '')}
                </text>
              )}
            </g>
          );
        })}
        <text className="chart__axis" x={PAD.l - 6} y={PAD.t + 8} textAnchor="end">{max}</text>
      </svg>
    </div>
  );
}
