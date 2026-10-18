import React from 'react';
import { Icon } from '../brand/Icon.jsx';
const cx = (...a) => a.filter(Boolean).join(' ');
export function Select({ label, hint, options = [], size = 'md', className, id, ...rest }) {
  return (
    <label className={cx('fse-field', className)}>
      {label && <span className="fse-field__label">{label}</span>}
      <span className={cx('fse-input', 'fse-select', size === 'sm' && 'fse-input--sm')}>
        <select id={id} {...rest}>{options.map(o => typeof o === 'string' ? <option key={o} value={o}>{o}</option> : <option key={o.value} value={o.value}>{o.label}</option>)}</select>
        <Icon name="chevrons-up-down" size={12} />
      </span>
      {hint && <span className="fse-field__hint">{hint}</span>}
    </label>
  );
}