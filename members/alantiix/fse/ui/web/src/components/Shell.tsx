import { useEffect, useState, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Badge, Banner, Breadcrumbs, Button, IconButton, Kbd, PoweredBy, SideNav, StatusDot, Wordmark } from '../ds/index.js';
import { api } from '../lib/api';
import { login, useSession, useSessionExpired } from '../lib/session';
import { useLive, useNow } from '../lib/hooks';
import { paperLabel } from '../lib/paperLabel';
import { CommandPalette } from './CommandPalette';

export const NAV = [
  { title: 'Operate', items: [
    { value: 'live', label: 'Live runner', icon: 'radio' },
    { value: 'cards', label: 'Finding cards', icon: 'inbox' },
    { value: 'api-logs', label: 'API logs', icon: 'file-text' },
    { value: 'jobs', label: 'Jobs', icon: 'terminal' },
  ] },
  { title: 'Research', items: [
    { value: 'workflows', label: 'Workflows', icon: 'workflow' },
    { value: 'runs', label: 'Runs', icon: 'history' },
    { value: 'experiments', label: 'Experiments', icon: 'flask-conical' },
    { value: 'commands', label: 'All commands', icon: 'list' },
  ] },
  { title: 'Configure', items: [
    { value: 'configs', label: 'Configs', icon: 'sliders-horizontal' },
    { value: 'plugins', label: 'Plugins', icon: 'puzzle' },
    { value: 'secrets', label: 'Secrets', icon: 'key-round' },
  ] },
  { title: 'Monitor', items: [
    { value: 'analytics', label: 'Analytics', icon: 'chart-line' },
    { value: 'health', label: 'Health', icon: 'activity' },
    { value: 'system', label: 'System', icon: 'server' },
    { value: 'audit', label: 'Audit log', icon: 'scroll-text' },
  ] },
];

const LABELS: Record<string, string> = Object.fromEntries(
  NAV.flatMap((s) => s.items.map((i) => [i.value, i.label.toLowerCase()])),
);

function useTheme(): [string, (t: string) => void] {
  const [theme, setTheme] = useState(() => localStorage.getItem('fse-theme') || 'dark');
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('fse-theme', theme);
  }, [theme]);
  return [theme, setTheme];
}

function useClock(): string {
  const fmt = () => new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).format(new Date()) + ' ET';
  const [now, setNow] = useState(fmt);
  useEffect(() => {
    const t = setInterval(() => setNow(fmt()), 1000);
    return () => clearInterval(t);
  }, []);
  return now;
}

function StatusBar() {
  const { snap, connected } = useLive();
  const clock = useClock();
  const hb = snap?.heartbeat;
  const running = snap?.runner?.state === 'running';
  const halted = snap?.blocks.halted;
  useNow(60_000); // re-render so a stale heartbeat turns the label idle without new data
  const paper = paperLabel(snap, Date.now());
  return (
    <footer className="statusbar">
      <StatusDot status={halted ? 'halted' : running ? 'live' : 'idle'}
        label={halted ? 'Runner halted' : running ? 'Runner running' : 'Runner stopped'} />
      <StatusDot status={!hb ? 'idle' : hb.connected ? 'ok' : 'warning'}
        label={hb
          ? `ProjectX ${hb.transport ?? 'unknown'}${hb.provisional ? ' (hub)' : ''}`
          : (running ? 'ProjectX starting' : 'ProjectX unavailable')} />
      <span className="sb-lo"><StatusDot status={paper.status} label={paper.text} /></span>
      <StatusDot status={connected ? 'ok' : 'warning'}
        label={connected ? 'Console live' : 'Console reconnecting'} />
      <div className="statusbar__grow" />
      <span className="sb-hide-sm"><PoweredBy /></span>
      <span className="statusbar__rule sb-lo" />
      <span className="sb-lo">{snap?.run_dir ?? 'no runner run'}</span>
      <span className="sb-hide-sm">{clock}</span>
    </footer>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const session = useSession();
  const expired = useSessionExpired();
  const [theme, setTheme] = useTheme();
  const [palette, setPalette] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const parts = pathname.split('/').filter(Boolean);
  const page = parts[0] || 'live';

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPalette((p) => !p);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // Close the mobile drawer when the route changes (nav item or breadcrumb).
  useEffect(() => { setNavOpen(false); }, [pathname]);

  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setNavOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navOpen]);

  const crumbs = [
    { label: 'fse', onClick: () => navigate('/live') },
    { label: LABELS[page] ?? page, onClick: () => navigate(`/${page}`) },
    ...parts.slice(1).map((p) => ({ label: decodeURIComponent(p) })),
  ];

  const logout = async () => {
    const r = await api.post<{ logout_url: string | null }>('/auth/logout');
    window.location.assign(r.logout_url ?? '/');
  };

  const goNav = (v: string) => {
    navigate(`/${v}`);
    setNavOpen(false);
  };

  return (
    <div className={`shell${navOpen ? ' shell--nav-open' : ''}`}>
      <header className="topbar">
        <IconButton className="topbar__menu" icon="menu" label="Open navigation" size="sm"
          onClick={() => setNavOpen(true)} />
        <Wordmark size="sm" />
        <span className="topbar__sep" />
        <div className="topbar__crumbs"><Breadcrumbs items={crumbs} className="fse-crumbs--bar" /></div>
        <Button className="topbar__cmd" size="sm" variant="ghost" icon="search" kbd="⌘K" onClick={() => setPalette(true)}>
          Run a command
        </Button>
        <Badge mode="paper" dot />
        <span className="topbar__user">{session.user.email}</span>
        <IconButton icon={theme === 'light' ? 'moon' : 'sun'} label="Toggle theme" size="sm"
          onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')} />
        <IconButton icon="log-out" label="Log out" size="sm" onClick={logout} />
      </header>
      <div className="shell__mid">
        {navOpen && (
          <button type="button" className="shell__scrim" aria-label="Close navigation"
            onClick={() => setNavOpen(false)} />
        )}
        <aside className={`shell__nav${navOpen ? ' shell__nav--open' : ''}`} aria-label="Primary">
          <SideNav sections={NAV} value={page} onChange={goNav} />
        </aside>
        <main className="shell__main">
          {expired && (
            <Banner tone="warning" flush title="Session expired"
              actions={<Button size="sm" variant="primary" onClick={login}>Log in again</Button>}>
              Unsaved edits on this page stay until you leave it.
            </Banner>
          )}
          {children}
        </main>
      </div>
      <StatusBar />
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
      <span hidden><Kbd>⌘</Kbd></span>
    </div>
  );
}
