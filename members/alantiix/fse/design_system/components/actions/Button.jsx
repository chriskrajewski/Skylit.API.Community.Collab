import React from 'react';
import { Icon } from '../brand/Icon.jsx';
const cx = (...a) => a.filter(Boolean).join(' ');
export function Button({ variant = 'secondary', size = 'md', icon, iconRight, kbd, children, className, type = 'button', ...rest }) {
  const is = size === 'sm' ? 12 : 14;
  return (
    <button type={type} className={cx('fse-btn', 'fse-btn--' + variant, size !== 'md' && 'fse-btn--' + size, className)} {...rest}>
      {icon && <Icon name={icon} size={is} />}
      {children}
      {iconRight && <Icon name={iconRight} size={is} />}
      {kbd && <span className="fse-btn__kbd">{kbd}</span>}
    </button>
  );
}