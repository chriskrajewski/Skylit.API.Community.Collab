import React from 'react';
export function Sparkline({ values = [], width = 120, height = 28, tone = 'accent', baseline = true, fill = false, responsive = false, className }) {
  if (!values.length) return null;
  const min = Math.min(0, ...values), max = Math.max(0, ...values), span = max - min || 1;
  const x = i => (i / Math.max(1, values.length - 1)) * width, y = v => height - 1 - ((v - min) / span) * (height - 2);
  const d = values.map((v, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join(' ');
  const t = tone === 'auto' ? (values[values.length - 1] >= 0 ? 'bull' : 'bear') : tone;
  const col = { accent: 'var(--accent)', bull: 'var(--color-bull)', bear: 'var(--color-bear)', muted: 'var(--fg-3)', ink: 'var(--fg-1)' }[t] || 'var(--fg-1)';
  return (
    <svg className={className} width={responsive ? '100%' : width} height={height} viewBox={'0 0 ' + width + ' ' + height} preserveAspectRatio={responsive ? 'none' : undefined} style={{ display: 'block', overflow: 'visible' }}>
      {baseline && <line x1="0" x2={width} y1={y(0)} y2={y(0)} stroke="var(--line-2)" strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />}
      {fill && <path d={d + ' L' + width + ' ' + y(0) + ' L0 ' + y(0) + 'Z'} fill={col} opacity=".12" />}
      <path d={d} fill="none" stroke={col} strokeWidth="1.25" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}