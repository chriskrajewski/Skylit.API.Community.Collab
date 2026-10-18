import React from 'react';
export function PoweredBy({ label = 'Data powered by', suffix = 'API', logoSrc, href = 'https://www.skylit.ai/', size = 'sm', className }) {
  const fs = size === 'md' ? 13 : 11, lh = size === 'md' ? 22 : 16;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={'fse-powered' + (className ? ' ' + className : '')} style={{ fontSize: fs }}>
      <span className="fse-powered__label">{label}</span>
      {logoSrc && <img src={logoSrc} alt="" aria-hidden="true" className="fse-powered__logo" style={{ height: lh, width: lh }} />}
      <span className="fse-powered__name">Skylit.ai</span>
      {suffix && <span className="fse-powered__label">{suffix}</span>}
    </a>
  );
}