import { useEffect, useRef } from 'react';

function lineClass(line: string): string {
  if (line.startsWith('$ ')) return 'logview__line--cmd';
  if (line.startsWith('error:') || line.startsWith('Traceback')) return 'logview__line--err';
  if (/^exit -?\d+|^exit none/.test(line) || line.startsWith('runner exited')) {
    return 'logview__line--exit';
  }
  return '';
}

/** Command output, following the end while the operator is at the bottom.
 *  `newestFirst`: the lines are newest first, so the view stays at the top. */
export function LogView({ lines, tall, empty = 'No output yet.', newestFirst = false }: {
  lines: string[];
  tall?: boolean;
  empty?: string;
  newestFirst?: boolean;
}) {
  const ref = useRef<HTMLPreElement>(null);
  const atBottom = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (el && atBottom.current && !newestFirst) el.scrollTop = el.scrollHeight;
  }, [lines, newestFirst]);

  return (
    <pre
      ref={ref}
      className={'logview' + (tall ? ' logview--tall' : '')}
      aria-live="polite"
      onScroll={(e) => {
        const el = e.currentTarget;
        atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      }}
    >
      {lines.length === 0 ? <span className="muted">{empty}</span> : lines.map((l, i) => (
        <div key={i} className={lineClass(l)}>{l || ' '}</div>
      ))}
    </pre>
  );
}
