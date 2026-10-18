import React from 'react';
import { Icon } from '../brand/Icon.jsx';
const cx = (...a) => a.filter(Boolean).join(' ');
export function Input({ label, hint, error, warning, icon, prefix, suffix, size = 'md', disabled, className, id, ...rest }) {
  const fid = id || (label ? 'in-' + String(label).replace(/\W+/g, '-').toLowerCase() : undefined);
  return (
    <label className={cx('fse-field', className)} htmlFor={fid}>
      {label && <span className="fse-field__label">{label}</span>}
      <span className={cx('fse-input', size === 'sm' && 'fse-input--sm', error && 'fse-input--error', !error && warning && 'fse-input--warning', disabled && 'fse-input--disabled')}>
        {icon && <Icon name={icon} size={12} style={{ color: 'var(--text-tertiary)' }} />}
        {prefix && <span className="fse-input__affix">{prefix}</span>}
        <input id={fid} disabled={disabled} {...rest} />
        {suffix && <span className="fse-input__affix">{suffix}</span>}
      </span>
      {(error || warning || hint) && <span className={cx('fse-field__hint', error && 'fse-field__hint--error', !error && warning && 'fse-field__hint--warning')}>{error || warning || hint}</span>}
    </label>
  );
}