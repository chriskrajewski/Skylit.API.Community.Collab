import React from 'react';
const S = { sm: [15, 11], md: [20, 12], lg: [32, 15], xl: [64, 22] };
export function Wordmark({ size = 'md', lockup = true, stacked = false, className }) {
  const [m, n] = S[size] || S.md;
  return (
    <span className={'fse-wordmark' + (className ? ' ' + className : '')} aria-label="fse — futures strategy engine">
      <span className="fse-wordmark__mark" style={{ fontSize: m }}>fse<i>_</i></span>
      {lockup && <span className="fse-wordmark__rule" />}
      {lockup && <span className="fse-wordmark__name" style={{ fontSize: n }}>{stacked ? <>futures<br />strategy engine</> : 'futures strategy engine'}</span>}
    </span>
  );
}