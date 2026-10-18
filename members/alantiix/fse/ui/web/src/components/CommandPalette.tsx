import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Badge, Dialog, Input } from '../ds/index.js';
import { useCatalog } from '../lib/hooks';

export function commandHref(id: string): string {
  return `/commands/${encodeURIComponent(id)}`;
}

/** ⌘K: every command of every engine CLI, filtered by text. */
export function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data } = useCatalog();
  const [q, setQ] = useState('');
  const [sel, setSel] = useState(0);
  const navigate = useNavigate();
  const list = useMemo(() => {
    const words = q.toLowerCase().split(/\s+/).filter(Boolean);
    return (data?.commands ?? []).filter((c) => {
      const hay = `${c.id} ${c.help}`.toLowerCase();
      return words.every((w) => hay.includes(w));
    });
  }, [data, q]);
  useEffect(() => setSel(0), [q, open]);

  const go = (id: string) => {
    onClose();
    setQ('');
    navigate(commandHref(id));
  };

  return (
    <Dialog open={open} title="Run a command" onClose={onClose} width={720}>
      <div className="palette">
        <Input icon="search" autoFocus placeholder="pull, backtest, experiment sweep…" value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setSel((s) => Math.min(s + 1, list.length - 1)); }
            if (e.key === 'ArrowUp') { e.preventDefault(); setSel((s) => Math.max(s - 1, 0)); }
            if (e.key === 'Enter' && list[sel]) go(list[sel].id);
          }} />
        <div className="palette__list" role="listbox">
          {list.map((c, i) => (
            <button key={c.id} type="button" role="option" aria-selected={i === sel}
              className="palette__item" onClick={() => go(c.id)}>
              <span className="nowrap">{c.id}</span>
              {c.disabled_reason ? <Badge variant="warning-outline">disabled</Badge>
                : <Badge variant="outline">{c.placement}</Badge>}
              <span className="palette__help">{c.help}</span>
            </button>
          ))}
          {list.length === 0 && <div className="empty">No command matches.</div>}
        </div>
      </div>
    </Dialog>
  );
}
