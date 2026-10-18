import React from 'react';
import { Icon } from '../brand/Icon.jsx';
import { IconButton } from '../actions/IconButton.jsx';
const IC = { info: 'info', accent: 'zap', ok: 'circle-check', warning: 'triangle-alert', danger: 'octagon-alert' };
export function Toast({ tone = 'info', title, message, icon, onClose, action }) {
  return (
    <div role="status" className={'fse-toast fse-toast--' + tone}>
      <Icon name={icon || IC[tone] || 'info'} size={14} className="fse-toast__icon" />
      <div className="fse-toast__body"><span className="fse-toast__title">{title}</span>{message && <span className="fse-toast__msg">{message}</span>}{action}</div>
      {onClose && <IconButton icon="x" label="Dismiss" size="sm" onClick={onClose} />}
    </div>
  );
}