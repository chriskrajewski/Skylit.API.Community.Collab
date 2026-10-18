import React from 'react';
export function MeterBar({ label, value, max = 100, display, tone = 'ink', labelWidth, className }) {
  const pct = Math.max(0, Math.min(100, (value / (max || 1)) * 100));
  return (
    <div className={'fse-meter' + (tone !== 'ink' ? ' fse-meter--' + tone : '') + (className ? ' ' + className : '')} style={labelWidth ? { '--meter-label': typeof labelWidth === 'number' ? labelWidth + 'px' : labelWidth } : undefined}>
      <span className="fse-meter__label" title={typeof label === 'string' ? label : undefined}>{label}</span>
      <span className="fse-meter__track"><span className="fse-meter__fill" style={{ width: pct + '%', display: 'block' }} /></span>
      <span className="fse-meter__value">{display ?? value}</span>
    </div>
  );
}