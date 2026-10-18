import React from 'react';
const cx = (...a) => a.filter(Boolean).join(' ');
export function Switch({ label, disabled, className, ...rest }) {
  return (
    <label className={cx('fse-switch', disabled && 'fse-switch--disabled', className)}>
      <input type="checkbox" role="switch" disabled={disabled} {...rest} />
      <span className="fse-switch__track" />
      {label}
    </label>
  );
}