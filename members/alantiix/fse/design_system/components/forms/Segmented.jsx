import React from 'react';
export function Segmented({ options = [], value, onChange, className }) {
  return (
    <div role="group" className={'fse-seg' + (className ? ' ' + className : '')}>
      {options.map(o => { const v = typeof o === 'string' ? o : o.value; const l = typeof o === 'string' ? o : o.label;
        return <button key={v} type="button" aria-pressed={v === value} onClick={() => onChange && onChange(v)}>{l}</button>; })}
    </div>
  );
}