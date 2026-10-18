import React from 'react';
import { IconButton } from '../actions/IconButton.jsx';
import { Icon } from '../brand/Icon.jsx';
export function Dialog({ open = true, title, children, footer, onClose, tone, inline, width, alarmLabel }) {
  if (!open) return null;
  return (
    <div className={'fse-scrim' + (inline ? ' fse-scrim--inline' : '')} onClick={e => { if (e.target === e.currentTarget && onClose) onClose(); }}>
      <div role="dialog" aria-modal="true" className={'fse-dialog' + (tone === 'danger' ? ' fse-dialog--danger' : '')} style={width ? { maxWidth: width } : undefined}>
        {tone === 'danger' && <div className="fse-dialog__alarm"><Icon name="triangle-alert" size={12} />{alarmLabel || 'Affects live state'}</div>}
        <div className="fse-dialog__head"><h2 className="fse-dialog__title">{title}</h2>{onClose && <IconButton icon="x" label="Close" size="sm" onClick={onClose} />}</div>
        <div className="fse-dialog__body">{children}</div>
        {footer && <div className="fse-dialog__foot">{footer}</div>}
      </div>
    </div>
  );
}