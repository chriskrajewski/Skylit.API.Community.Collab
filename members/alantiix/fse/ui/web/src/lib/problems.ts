// Routes `POST /api/plugins/check` problem strings to the form they belong to (design §7.6).

export interface RoutedProblems {
  /** `accounts[j]…` → the account form j (field and cross-reference errors alike). */
  accounts: Map<number, string[]>;
  /** `plugins[i].account…` → under plugin card i's Account select. */
  pluginAccount: Map<number, string[]>;
  /** Everything else: the page's top banner. */
  rest: string[];
}

const ACCOUNT_RE = /^accounts\[(\d+)\]/;
const PLUGIN_ACCOUNT_RE = /^plugins\[(\d+)\]\.account/;

function push(map: Map<number, string[]>, key: number, value: string): void {
  const list = map.get(key);
  if (list) list.push(value);
  else map.set(key, [value]);
}

export function routeProblems(problems: string[]): RoutedProblems {
  const out: RoutedProblems = { accounts: new Map(), pluginAccount: new Map(), rest: [] };
  for (const p of problems) {
    const a = ACCOUNT_RE.exec(p);
    if (a) { push(out.accounts, Number(a[1]), p); continue; }
    const pa = PLUGIN_ACCOUNT_RE.exec(p);
    if (pa) { push(out.pluginAccount, Number(pa[1]), p); continue; }
    out.rest.push(p);
  }
  return out;
}
