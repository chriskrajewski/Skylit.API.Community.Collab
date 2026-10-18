import React from 'react';
function tok(line) {
  const out = []; const ci = line.search(/(^|\s)#/);
  const code = ci >= 0 ? line.slice(0, ci) : line, com = ci >= 0 ? line.slice(ci) : '';
  const m = code.match(/^(\s*-?\s*)([\w.]+)(:)(.*)$/);
  if (m) { out.push(m[1], <span key="k" className="k">{m[2]}</span>, <span key="p" className="p">{m[3]}</span>);
    const v = m[4]; out.push(<span key="v" className={/\btrue\b/.test(v) && !/\bfalse\b/.test(v) ? 't' : /\bfalse\b/.test(v) ? 'f' : /\bnull\b/.test(v) ? 'b' : 'v'}>{v}</span>); }
  else out.push(<span key="v" className="v">{code}</span>);
  if (com) out.push(<span key="c" className="c">{com}</span>);
  return out;
}
export function CodeBlock({ code = '', highlight = [], numbers = true, plain, maxHeight, className }) {
  const lines = code.replace(/\n$/, '').split('\n');
  return (
    <pre className={'fse-code' + (className ? ' ' + className : '')} style={maxHeight ? { maxHeight } : undefined}>
      <table><tbody>{lines.map((l, i) => <tr key={i} className={highlight.includes(i + 1) ? 'is-hl' : undefined}>{numbers && <td className="n">{i + 1}</td>}<td>{plain ? l : tok(l)}{'\n'}</td></tr>)}</tbody></table>
    </pre>
  );
}