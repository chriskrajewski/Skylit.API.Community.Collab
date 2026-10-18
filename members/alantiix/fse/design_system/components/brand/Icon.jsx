import React from 'react';
const BASE = 'https://unpkg.com/lucide-static@0.469.0/icons/';
const cache = {};
function load(name) {
  if (!cache[name]) cache[name] = fetch(BASE + name + '.svg').then(r => r.ok ? r.text() : '').then(t => t.replace(/<!--[\s\S]*?-->/g, '').replace(/width="24"|height="24"/g, '').replace('<svg', '<svg width="100%" height="100%"')).catch(() => '');
  return cache[name];
}
export function Icon({ name, size = 14, className, style, title, ...rest }) {
  const [svg, setSvg] = React.useState(() => (typeof cache[name] === 'string' ? cache[name] : ''));
  React.useEffect(() => { let on = true; load(name).then(s => { if (on) setSvg(s); }); return () => { on = false; }; }, [name]);
  return <span role={title ? 'img' : undefined} aria-label={title} aria-hidden={title ? undefined : true} className={'fse-icon' + (className ? ' ' + className : '')} style={{ width: size, height: size, ...style }} dangerouslySetInnerHTML={{ __html: svg }} {...rest} />;
}
