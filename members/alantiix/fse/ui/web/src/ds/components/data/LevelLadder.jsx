import React from 'react';
const KIND = { king: 'King', ceiling: 'Ceiling', floor: 'Floor', gatekeeper: 'Gatekeeper', air: 'Air pocket' };
export function LevelLadder({ levels = [], spot, spotLabel = 'Spot', className }) {
  const sorted = [...levels].sort((a, b) => b.level - a.level);
  const maxAbs = Math.max(1, ...levels.map(l => Math.abs(l.value || 0)));
  const rows = []; let placed = spot == null;
  const spotRow = <div key="spot" className="fse-ladder__spot"><span className="fse-ladder__kind">{spotLabel}</span><span className="fse-ladder__lvl">{Number(spot).toFixed(2)}</span><span /><hr /></div>;
  sorted.forEach((l, i) => {
    if (!placed && l.level < spot) { rows.push(spotRow); placed = true; }
    const cls = l.kind === 'king' ? 'king' : l.kind === 'ceiling' ? 'ceiling' : l.kind === 'floor' ? 'floor' : l.kind === 'air' ? 'air' : 'gk';
    const w = Math.round((Math.abs(l.value || 0) / maxAbs) * 100);
    rows.push(
      <div key={i} className={'fse-ladder__row fse-ladder__row--' + cls}>
        <span className="fse-ladder__kind">{l.label || KIND[l.kind] || l.kind}</span>
        <span className="fse-ladder__lvl">{Number(l.level).toFixed(2)}</span>
        <span className="fse-ladder__strike">{l.strike}</span>
        <span className="fse-ladder__bar"><span style={{ width: Math.max(w, 2) + '%' }} /></span>
      </div>);
  });
  if (!placed) rows.push(spotRow);
  return <div className={'fse-ladder' + (className ? ' ' + className : '')}>{rows}</div>;
}