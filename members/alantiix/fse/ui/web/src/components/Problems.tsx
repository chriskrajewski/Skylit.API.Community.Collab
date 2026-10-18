import type { Problem, Validation } from '../lib/types';

/** Validation results: errors bear, operator-required warnings warning (DS validation rules). */
export function Problems({ validation, onPick }: { validation: Validation | null; onPick?: (p: Problem) => void }) {
  if (!validation) return null;
  const row = (p: Problem, kind: 'error' | 'warning', i: number) => (
    <div key={`${kind}${i}`} className={`problem problem--${kind}`}>
      <span className="problem__kind">{kind}</span>
      {p.line !== null && onPick
        ? <button type="button" className="link" onClick={() => onPick(p)}>line {p.line}</button>
        : p.line !== null ? <span className="muted">line {p.line}</span> : null}
      <span className="problem__path">{p.key_path || '(file)'}</span>
      <span className="muted">{p.message}</span>
    </div>
  );
  if (validation.ok && validation.warnings.length === 0) {
    return <div className="small text-accent">Valid. The engine accepts this file.</div>;
  }
  return (
    <div className="problems">
      {validation.errors.map((p, i) => row(p, 'error', i))}
      {validation.warnings.map((p, i) => row(p, 'warning', i))}
    </div>
  );
}
