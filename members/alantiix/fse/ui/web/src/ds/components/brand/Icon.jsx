import React from 'react';
import { iconSvg } from './icons.js';
// Console change from the design system: icons are bundled from lucide-static
// (see icons.js) instead of fetched from unpkg at run time, so the console works
// under a strict CSP with no third-party requests. Rendering is unchanged.
export function Icon({ name, size = 14, className, style, title, ...rest }) {
  const svg = iconSvg(name);
  return <span role={title ? 'img' : undefined} aria-label={title} aria-hidden={title ? undefined : true} className={'fse-icon' + (className ? ' ' + className : '')} style={{ width: size, height: size, ...style }} dangerouslySetInnerHTML={{ __html: svg }} {...rest} />;
}
