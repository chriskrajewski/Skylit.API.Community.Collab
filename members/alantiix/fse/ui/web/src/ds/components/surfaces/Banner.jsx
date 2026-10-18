import React from 'react';
import { Icon } from '../brand/Icon.jsx';
const IC = { info: 'info', warning: 'triangle-alert', alarm: 'octagon-x' };
export function Banner({ tone = 'info', title, children, actions, flush, icon, className }) {
  return (
    <div role={tone === 'alarm' ? 'alert' : 'status'} className={'fse-banner fse-banner--' + tone + (flush ? ' fse-banner--flush' : '') + (className ? ' ' + className : '')}>
      <Icon name={icon || IC[tone]} size={16} />
      {title && <span className="fse-banner__title">{title}</span>}
      {children && <span className="fse-banner__msg">{children}</span>}
      {actions && <span className="fse-banner__actions">{actions}</span>}
    </div>
  );
}