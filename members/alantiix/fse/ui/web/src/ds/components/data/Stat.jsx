import React from 'react';
const cx = (...a) => a.filter(Boolean).join(' ');
export function Stat({ label, value, unit, sub, tone, size = 'md', className }) {
  return (
    <div className={cx('fse-stat', size !== 'md' && 'fse-stat--' + size, className)}>
      <span className="fse-stat__label">{label}</span>
      <span className={cx('fse-stat__value', tone && 'fse-' + tone)}>{value}{unit && <span className="fse-stat__unit">{unit}</span>}</span>
      {sub && <span className="fse-stat__sub">{sub}</span>}
    </div>
  );
}