import React from 'react';
export function Tabs({ items = [], value, onChange, className }) {
  return (
    <div role="tablist" className={'fse-tabs' + (className ? ' ' + className : '')}>
      {items.map(t => <button key={t.value} role="tab" aria-selected={t.value === value} onClick={() => onChange && onChange(t.value)}>{t.label}{t.count != null && <span className="fse-tabs__count">{t.count}</span>}</button>)}
    </div>
  );
}