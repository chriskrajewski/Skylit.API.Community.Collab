import React from 'react';
const cx = (...a) => a.filter(Boolean).join(' ');
export function DataTable({ columns = [], rows = [], rowKey = 'id', dense, onRowClick, selectedKey, className, maxHeight }) {
  return (
    <div className="fse-table-wrap" style={maxHeight ? { maxHeight } : undefined}>
      <table className={cx('fse-table', dense && 'fse-table--dense', onRowClick && 'fse-table--hover', className)}>
        <thead><tr>{columns.map(c => <th key={c.key} className={c.align === 'right' ? 'is-num' : undefined} style={c.width ? { width: c.width } : undefined}>{c.label}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => { const k = r[rowKey] ?? i;
          return <tr key={k} className={k === selectedKey ? 'is-selected' : undefined} onClick={onRowClick ? () => onRowClick(r) : undefined}>
            {columns.map(c => <td key={c.key} className={cx(c.align === 'right' && 'is-num', c.muted && 'is-muted')}>{c.render ? c.render(r[c.key], r) : r[c.key]}</td>)}
          </tr>; })}</tbody>
      </table>
    </div>
  );
}