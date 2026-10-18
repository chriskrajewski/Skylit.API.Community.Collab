import React from 'react';
import { Icon } from '../brand/Icon.jsx';
export function Panel({ title, subtitle, icon, actions, footer, flush, children, className, style }) {
  return (
    <section className={'fse-panel' + (className ? ' ' + className : '')} style={style}>
      {(title || actions) && <header className="fse-panel__head">
        <div className="fse-panel__title">{icon && <Icon name={icon} size={14} style={{ color: 'var(--text-tertiary)' }} />}{title}{subtitle && <span className="fse-panel__sub">{subtitle}</span>}</div>
        {actions && <div className="fse-panel__actions">{actions}</div>}
      </header>}
      <div className={'fse-panel__body' + (flush ? ' fse-panel__body--flush' : '')}>{children}</div>
      {footer && <footer className="fse-panel__foot">{footer}</footer>}
    </section>
  );
}