import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';
import { Toast } from '../ds/index.js';

type Tone = 'info' | 'accent' | 'ok' | 'warning' | 'danger';
interface Item { id: number; tone: Tone; title: string; message?: string }

const ToastContext = createContext<(tone: Tone, title: string, message?: string) => void>(() => {});

export function useToast() {
  return useContext(ToastContext);
}

/** A small queue for the design-system Toast (the DS ships the toast, not a manager). */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Item[]>([]);
  const push = useCallback((tone: Tone, title: string, message?: string) => {
    const id = Date.now() + Math.random();
    setItems((prev) => [...prev.slice(-3), { id, tone, title, message }]);
    setTimeout(() => setItems((prev) => prev.filter((t) => t.id !== id)), tone === 'danger' ? 12000 : 6000);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="toast-stack">
        {items.map((t) => (
          <Toast key={t.id} tone={t.tone} title={t.title} message={t.message}
            onClose={() => setItems((prev) => prev.filter((x) => x.id !== t.id))} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}
