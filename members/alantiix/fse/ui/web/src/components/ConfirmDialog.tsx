import { useState, type ReactNode } from 'react';
import { Button, Dialog, Input } from '../ds/index.js';

/**
 * A confirmation dialog. ``danger`` gives the alarm-tone dialog the design system
 * reserves for halts and kill switches; ``typed`` asks the operator to type a word.
 */
export function ConfirmDialog({
  open, title, children, confirmLabel, onConfirm, onClose, danger, alarmLabel, typed, icon, busy,
}: {
  open: boolean;
  title: ReactNode;
  children?: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onClose: () => void;
  danger?: boolean;
  alarmLabel?: string;
  typed?: string;
  icon?: string;
  busy?: boolean;
}) {
  const [text, setText] = useState('');
  const ready = !typed || text.trim() === typed;
  return (
    <Dialog
      open={open}
      title={title}
      tone={danger ? 'danger' : 'default'}
      alarmLabel={alarmLabel}
      onClose={() => { setText(''); onClose(); }}
      footer={<>
        <Button variant="ghost" onClick={() => { setText(''); onClose(); }}>Cancel</Button>
        <Button variant={danger ? 'danger' : 'primary'} icon={icon} disabled={!ready || busy}
          onClick={() => { setText(''); onConfirm(); }}>{confirmLabel}</Button>
      </>}
    >
      <div className="stack">
        {children}
        {typed && (
          <Input label={`Type ${typed} to confirm`} value={text} autoFocus
            onChange={(e) => setText(e.target.value)} />
        )}
      </div>
    </Dialog>
  );
}
