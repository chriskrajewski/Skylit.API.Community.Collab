import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { ApiError, api, onSessionExpired, setCsrf } from './api';
import type { Session } from './types';

const SessionContext = createContext<Session | null>(null);
const ExpiredContext = createContext(false);

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error('useSession outside SessionProvider');
  return s;
}

export function useSessionExpired(): boolean {
  return useContext(ExpiredContext);
}

export function login(): void {
  window.location.assign('/auth/login');
}

/** Loads the session; without one, sends the browser to the login page. */
export function SessionProvider({ children, fallback }: { children: ReactNode; fallback: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [expired, setExpired] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.get<Session>('/api/session').then(
      (s) => {
        if (!live) return;
        setCsrf(s.csrf);
        setSession(s);
      },
      (err) => {
        if (!live) return;
        if (err instanceof ApiError && err.status === 401) login();
        else setError(err instanceof Error ? err.message : String(err));
      },
    );
    const off = onSessionExpired(() => setExpired(true));
    return () => {
      live = false;
      off();
    };
  }, []);

  if (error) return <div className="boot">The console API is unavailable: {error}</div>;
  if (!session) return <>{fallback}</>;
  return (
    <SessionContext.Provider value={session}>
      <ExpiredContext.Provider value={expired}>{children}</ExpiredContext.Provider>
    </SessionContext.Provider>
  );
}

/** For tests: a fixed session. */
export function TestSession({ session, children }: { session: Session; children: ReactNode }) {
  setCsrf(session.csrf);
  return <SessionContext.Provider value={session}>{children}</SessionContext.Provider>;
}
