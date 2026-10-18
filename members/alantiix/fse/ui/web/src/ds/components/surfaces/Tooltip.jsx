import React from 'react';
export function Tooltip({ content, side = 'top', children }) {
  return <span className={'fse-tip' + (side === 'bottom' ? ' fse-tip--bottom' : '')}>{children}<span role="tooltip" className="fse-tip__bubble">{content}</span></span>;
}