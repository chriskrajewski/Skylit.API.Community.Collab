import React from 'react';
import { Icon } from '../brand/Icon.jsx';
const cx = (...a) => a.filter(Boolean).join(' ');
export function Checkbox({ label, disabled, className, ...rest }) {
  return (
    <label className={cx('fse-check', disabled && 'fse-check--disabled', className)}>
      <input type="checkbox" disabled={disabled} {...rest} />
      <span className="fse-check__box"><Icon name="check" size={10} /></span>
      {label}
    </label>
  );
}