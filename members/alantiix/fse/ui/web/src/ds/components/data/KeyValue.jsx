import React from 'react';
export function KeyValue({ items = [], columns = 1, className }) {
  return (
    <dl className={'fse-kv' + (columns > 1 ? ' fse-kv--cols' : '') + (className ? ' ' + className : '')} style={columns > 1 ? { '--kv-cols': columns } : undefined}>
      {items.map(([k, v], i) => <React.Fragment key={i}><dt>{k}</dt><dd>{v}</dd></React.Fragment>)}
    </dl>
  );
}