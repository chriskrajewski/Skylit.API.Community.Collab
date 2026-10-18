import React from 'react';
export function Breadcrumbs({ items = [], className }) {
  return (
    <nav aria-label="Breadcrumb" className={'fse-crumbs' + (className ? ' ' + className : '')}>
      {items.map((it, i) => { const last = i === items.length - 1;
        return <React.Fragment key={i}>
          {i > 0 && <span className="fse-crumbs__sep">/</span>}
          {last ? <span className="fse-crumbs__cur">{it.label}</span> : it.onClick ? <button type="button" onClick={it.onClick}>{it.label}</button> : <a href={it.href || '#'}>{it.label}</a>}
        </React.Fragment>; })}
    </nav>
  );
}