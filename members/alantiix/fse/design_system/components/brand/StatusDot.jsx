import React from 'react';
export function StatusDot({ status = 'idle', label, className }) {
  return <span className={'fse-dot fse-dot--' + status + (className ? ' ' + className : '')}><span className="fse-dot__d" />{label}</span>;
}