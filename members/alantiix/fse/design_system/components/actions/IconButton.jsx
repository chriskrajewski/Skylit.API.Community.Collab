import React from 'react';
import { Icon } from '../brand/Icon.jsx';
const cx = (...a) => a.filter(Boolean).join(' ');
export function IconButton({ icon, label, variant = 'ghost', size = 'md', className, type = 'button', ...rest }) {
  return (
    <button type={type} aria-label={label} title={label} className={cx('fse-btn', 'fse-btn--icon', 'fse-btn--' + variant, size !== 'md' && 'fse-btn--' + size, className)} {...rest}>
      <Icon name={icon} size={size === 'sm' ? 12 : size === 'lg' ? 16 : 14} />
    </button>
  );
}