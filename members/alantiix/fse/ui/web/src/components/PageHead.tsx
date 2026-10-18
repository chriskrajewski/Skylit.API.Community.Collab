import type { ReactNode } from 'react';

export function PageHead({ title, sub, actions, children }: {
  title: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="pagehead">
      <div className="pagehead__text">
        <h1>{title}</h1>
        {sub && <div className="pagehead__sub">{sub}</div>}
        {children}
      </div>
      {actions && <div className="pagehead__actions">{actions}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
