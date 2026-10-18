import React from 'react';
import { Icon } from '../brand/Icon.jsx';
export function SideNav({ sections = [], value, onChange, className }) {
  return (
    <nav className={'fse-nav' + (className ? ' ' + className : '')}>
      {sections.map((s, si) => (
        <div key={si} className="fse-nav__sec">
          {s.title && <div className="fse-nav__title">{s.title}</div>}
          {s.items.map(it => (
            <button key={it.value} type="button" className="fse-nav__item" aria-current={it.value === value ? 'page' : undefined} onClick={() => onChange && onChange(it.value)}>
              {it.icon && <Icon name={it.icon} size={14} />}<span className="l">{it.label}</span>{it.meta != null && <span className="fse-nav__meta">{it.meta}</span>}
            </button>))}
        </div>))}
    </nav>
  );
}