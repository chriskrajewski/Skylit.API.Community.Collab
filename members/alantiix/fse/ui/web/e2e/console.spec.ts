// Smoke suite (design 13.1): six flows in one browser context against the built SPA,
// the real API in dev-auth mode and a stub fse (api/tests/e2e_server.py).
// Every test ends with no CSP violation, no CSP console message and no page error.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { expect, test, type BrowserContext, type Locator, type Page, type TestInfo } from '@playwright/test';
import { parse, parseDocument } from 'yaml';

declare global {
  interface Window { __csp: string[] }
}

const CSP_CONSOLE = /Refused to|Content Security Policy/;
/** Where the review screenshots go (E2E_SCREENSHOTS, else test-results/screenshots). */
const SHOTS = process.env.E2E_SCREENSHOTS ?? 'test-results/screenshots';

let context: BrowserContext;
let page: Page;
const cspConsole: string[] = [];
const pageErrors: string[] = [];

test.describe.configure({ mode: 'serial' });

test.beforeAll(async ({ browser }) => {
  context = await browser.newContext();
  await context.addInitScript(() => {
    window.__csp = [];
    document.addEventListener('securitypolicyviolation', (e) => {
      window.__csp.push(`${e.violatedDirective} ${e.blockedURI}`);
    });
  });
  page = await context.newPage();
  page.on('console', (msg) => {
    if (CSP_CONSOLE.test(msg.text())) cspConsole.push(msg.text());
  });
  page.on('pageerror', (err) => pageErrors.push(err.message));
});

test.afterEach(async () => {
  const violations = await page.evaluate(() => window.__csp ?? []);
  expect(violations, 'CSP violations').toEqual([]);
  expect(cspConsole, 'CSP console messages').toEqual([]);
  expect(pageErrors, 'page errors').toEqual([]);
});

test.afterAll(async () => {
  await context.close();
});

/** The ds Panel (section.fse-panel) whose title line contains `title`. */
function panel(title: string | RegExp): Locator {
  return page.locator('section.fse-panel').filter({
    has: page.locator('.fse-panel__title', { hasText: title }),
  });
}

/** Screenshots go to E2E_SCREENSHOT_DIR when set (local review), else the test's own output dir. */
function screenshotPath(testInfo: TestInfo, name: string): string {
  const dir = process.env.E2E_SCREENSHOT_DIR;
  return dir ? path.join(dir, name) : testInfo.outputPath(name);
}

/** The <dd> next to the <dt> labelled `label` inside `scope` (ds KeyValue). */
function kv(scope: Locator, label: string): Locator {
  return scope.locator('dt', { hasText: label }).locator('xpath=following-sibling::dd[1]');
}

/** The value of the ds Stat labelled `label` inside `scope`. */
function stat(scope: Locator, label: string): Locator {
  return scope.locator('.fse-stat', { has: page.locator('.fse-stat__label', { hasText: label }) }).locator('.fse-stat__value');
}

/** The seeded console data folder (e2e_server.py writes the temp root to this file). */
function dataDir(): string {
  const root = fs.readFileSync(path.join(os.tmpdir(), 'fse-e2e-root.txt'), 'utf8').trim();
  return path.join(root, 'home', '.skylit-fse');
}

/** Rewrite a seeded file in place (a text edit of the codec form, then an atomic rename). */
function rewrite(file: string, edit: (text: string) => string): void {
  const text = fs.readFileSync(file, 'utf8');
  const next = edit(text);
  expect(next, `${file} changed`).not.toBe(text);
  fs.writeFileSync(`${file}.tmp`, next);
  fs.renameSync(`${file}.tmp`, file);
}

async function csrf(): Promise<string> {
  return (await (await page.request.get('/api/session')).json()).csrf;
}

/** The runner config's current text (the `runner` config the console runner settings name). */
async function runnerYaml(): Promise<string> {
  return (await (await page.request.get('/api/configs/runner/runner')).json()).text;
}

/** Design §13: a `runner` revision with `accounts: [topstep_a]` and `account: topstep_a` on plugin_a. */
async function saveAccountsRevision(): Promise<void> {
  const doc = parseDocument(await runnerYaml());
  if (doc.getIn(['accounts'])) return;
  doc.setIn(['accounts'], doc.createNode([{ id: 'topstep_a', preset: 'topstep_50k_combine' }]));
  const plugins = doc.toJSON().plugins as { id: string }[];
  doc.setIn(['plugins', plugins.findIndex((p) => p.id === 'plugin_a'), 'account'], 'topstep_a');
  const r = await page.request.post('/api/configs/runner/runner', {
    headers: { 'X-FSE-CSRF': await csrf() },
    data: { text: doc.toString(), message: 'e2e: paper account' },
  });
  expect(r.ok(), await r.text()).toBe(true);
}

test('1. log in: the shell renders', async () => {
  // Dev auth: no session sends the browser to /auth/login, which sets the cookie.
  await page.goto('/');
  await expect(page.locator('header.topbar')).toBeVisible();
  await expect(page).toHaveURL(/\/live$/);
  await expect(page.getByRole('heading', { name: 'Live runner' })).toBeVisible();
});

test('2. configs: edit playbook_baseline and save a revision', async () => {
  await page.goto('/configs/strategy/playbook_baseline');
  const editor = panel('playbook_baseline');
  await expect(editor).toBeVisible();
  const revisions = panel('Revisions').locator('tbody tr');
  const before = await revisions.count();
  const configId = editor.getByLabel('config_id');
  await configId.fill(`${await configId.inputValue()}_e2e`);
  await expect(editor.getByText('unsaved')).toBeVisible();
  await editor.getByPlaceholder('What changed (optional)').fill('e2e edit');
  await editor.getByRole('button', { name: 'Save revision' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved playbook_baseline' })).toBeVisible();
  await expect(revisions).toHaveCount(before + 1);
  const current = revisions.filter({ hasText: 'current' });
  await expect(current).toHaveCount(1);
  await expect(current).toContainText('e2e edit');
});

test('3. plugins: toggle a plugin and run the check', async () => {
  await page.goto('/plugins');
  const card = panel('fse.live.plugins.template:TemplatePlugin');
  const enabled = card.getByRole('switch', { name: 'enabled' });
  await expect(enabled).not.toBeChecked();
  await card.locator('label.fse-switch', { hasText: 'enabled' }).click();
  await expect(enabled).toBeChecked();
  await page.getByRole('button', { name: 'Check plugins' }).click();
  await expect(page.getByRole('status').filter({ hasText: /Check passed|problem\(s\)/ })).toBeVisible();
});

test('4. jobs: run fse scan-secrets and read its log', async () => {
  await page.goto('/commands');
  await page.getByPlaceholder('Filter commands').fill('scan-secrets');
  await page.getByRole('cell', { name: 'fse scan-secrets', exact: true }).click();
  await expect(page).toHaveURL(/\/commands\//);
  await page.getByRole('button', { name: 'Run', exact: true }).click();
  await expect(page).toHaveURL(/\/jobs\/[^/]+$/);
  await page.goto('/jobs');
  await page.getByRole('cell', { name: /scan-secrets/ }).first().click();
  await expect(page).toHaveURL(/\/jobs\/[^/]+$/);
  await expect(page.getByText('stub argv', { exact: false })).toBeVisible();
  await expect(page.locator('.fse-panel__actions').getByText('succeeded', { exact: true })).toBeVisible();
});

test('14. secrets: find my ProjectX accounts and use one as Practice', async () => {
  // The stub fse prints two canned accounts; the runner is not restarted here.
  await page.goto('/secrets');
  await page.getByRole('button', { name: 'Find my ProjectX accounts' }).click();
  await expect(page.getByRole('cell', { name: 'PRAC-FAKE-1', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Use PRAC-FAKE-1 as Practice' }).click();
  await expect(page.getByText('Restart the runner for it to read the new value.').first()).toBeVisible();
  const secret = page.locator('.secret-row').filter({ hasText: 'PRACTICE_ACCOUNT_ID' });
  await expect(secret.getByText('set', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Use PRAC-FAKE-1 as Practice' })).toBeDisabled();
});
test('5. runs: open backtest-e2e', async () => {
  await page.goto('/runs');
  await page.getByRole('cell', { name: 'backtest-e2e', exact: true }).click();
  await expect(page).toHaveURL(/\/runs\/backtest-e2e$/);
  await expect(page.getByText('They are not a forecast of future results.')).toBeVisible();
  await expect(page.locator('.stats')).toContainText('Trades');
  await expect(page.locator('.stats')).toContainText('Expectancy');
  await expect(panel('All metrics')).toBeVisible();
});

test('6. live: halt one plugin, then clear it', async () => {
  await page.goto('/live');
  const card = panel('E2E plugin');
  await expect(card).toBeVisible();
  await expect(card).toContainText('Waiting for the 10:00 entry');
  await expect(card).toContainText('Enter long at 10:00 ET');
  await expect(card).not.toContainText('"book"');
  await card.getByRole('button', { name: 'Halt', exact: true }).click();
  const halt = page.getByRole('dialog');
  await expect(halt).toContainText('Halt plugin e2e_plugin?');
  await halt.getByRole('button', { name: 'Halt', exact: true }).click();
  const blocked = card.getByRole('alert').filter({ hasText: 'Entries blocked' });
  await expect(blocked).toBeVisible();
  await expect(blocked).toContainText('HALT file');
  await card.getByRole('button', { name: 'Clear', exact: true }).click();
  const clear = page.getByRole('dialog');
  await expect(clear).toContainText('Clear blocks of e2e_plugin?');
  await clear.getByRole('button', { name: 'Clear', exact: true }).click();
  await expect(blocked).toHaveCount(0);
  await expect(card.getByRole('button', { name: 'Halt', exact: true })).toBeVisible();
});
test('7. shell: only the main pane scrolls, the chrome stays pinned', async () => {
  await page.setViewportSize({ width: 1280, height: 600 });
  const geometry = () =>
    page.evaluate(() => {
      const box = (sel: string) => document.querySelector(sel)!.getBoundingClientRect();
      const main = document.querySelector('main.shell__main') as HTMLElement;
      const root = document.scrollingElement as HTMLElement;
      return {
        innerHeight: window.innerHeight,
        docScrollHeight: root.scrollHeight,
        docScrollTop: root.scrollTop,
        mainScrollable: main.scrollHeight > main.clientHeight,
        topbarTop: box('header.topbar').top,
        topbarBottom: box('header.topbar').bottom,
        navTop: box('aside.shell__nav').top,
        navBottom: box('aside.shell__nav').bottom,
        statusTop: box('footer.statusbar').top,
        statusBottom: box('footer.statusbar').bottom,
      };
    });
  for (const route of ['/live', '/analytics', '/configs', '/plugins', '/secrets', '/health', '/system', '/audit']) {
    await page.goto(route);
    await expect(page.locator('main.shell__main h1').first()).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 500));
    const g = await geometry();
    expect(g.docScrollHeight, `${route}: page must not scroll`).toBeLessThanOrEqual(g.innerHeight);
    expect(g.docScrollTop, `${route}: scrollTop`).toBe(0);
    expect(g.topbarTop, `${route}: topbar top`).toBe(0);
    expect(Math.abs(g.statusBottom - g.innerHeight), `${route}: statusbar flush`).toBeLessThanOrEqual(1);
    expect(g.navTop, `${route}: nav top`).toBe(g.topbarBottom);
    expect(g.navBottom, `${route}: nav bottom`).toBe(g.statusTop);
  }
  // /plugins and /analytics overflow at this height: scrolling main must not move the chrome.
  for (const route of ['/plugins', '/analytics']) {
    await page.goto(route);
    await expect(page.locator('main.shell__main h1').first()).toBeVisible();
    // Measure once the data is in: the heading renders before the runner config, so a slow
    // response leaves /plugins at "Loading…" (short enough to fit) and main does not overflow yet.
    if (route === '/plugins') await expect(panel('Add a plugin')).toBeVisible();
    if (route === '/analytics') await expect(panel('Trades').locator('tbody tr').first()).toBeVisible();
    const before = await geometry();
    expect(before.mainScrollable, `${route}: main overflows`).toBe(true);
    await page.evaluate(() => {
      (document.querySelector('main.shell__main') as HTMLElement).scrollTop = 300;
    });
    const after = await geometry();
    expect(await page.evaluate(() => (document.querySelector('main.shell__main') as HTMLElement).scrollTop)).toBeGreaterThan(0);
    expect(after, `${route}: chrome did not move`).toEqual(before);
  }
});

test('10. live: the plugin card reads as labelled text with its paper account, the raw state is collapsed', async ({}, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/live');
  const card = panel(/^\s*Plugin A/);
  await expect(card).toBeVisible();
  // e2e_server.py seeds plugin_a with a 2-lot long open since 12:00, its stop, today's
  // journal and its status.json; the open P&L is the live mark of marks.json (main's live P&L).
  await expect(card.getByTestId('today-line')).toContainText(/^closed \+?\$8\.88 \+ open \$\d+\.\d{2} = \$\d+\.\d{2} · 13 entries · 13 closed$/);
  await expect(card.getByTestId('open-pnl-s99')).toHaveText(/^\+\$\d+\.\d{2} \(\+\d+ t\)$/);
  const acct = card.getByRole('region', { name: 'Paper account' });
  await expect(kv(acct, 'Account')).toContainText('topstep_a');
  await expect(kv(acct, 'Account')).toContainText('active');
  await expect(kv(acct, 'Balance')).toHaveText('$50,090.40');
  await expect(kv(acct, 'Contracts')).toHaveText(/^2 of \d+$/);
  for (const header of ['Side', 'Qty', 'Contract', 'Entry', 'Open P&L', 'Mark', 'Stop']) {
    await expect(card.getByRole('columnheader', { name: header, exact: true }).first()).toBeVisible();
  }
  // the working stop of an open position is its Stop column (main's card), not a working order
  const position = card.getByRole('row').filter({ has: page.getByTestId('open-pnl-s99') });
  for (const text of ['long', '2', 'MNQ', '20,000.00', '19,990.00']) await expect(position).toContainText(text);
  // The raw saved state is behind Details, not the default view.
  await expect(card.locator('pre')).toHaveCount(0);
  // main scrolled past the page head: the topbar, nav and status bar stay pinned (test 7)
  await card.evaluate((el) => el.scrollIntoView({ block: 'start' }));
  expect(await page.locator('main.shell__main').evaluate((m) => m.scrollTop)).toBeGreaterThan(0);
  await page.screenshot({ path: screenshotPath(testInfo, 'live-1280x800.png') });
  await card.getByRole('button', { name: 'Details' }).click();
  await expect(card.locator('pre')).toContainText('"book"');
  await card.getByRole('button', { name: 'Hide details' }).click();
});

test('11. runs: the Report tab renders report/report.md', async () => {
  await page.goto('/runs/backtest-e2e-sub');
  await page.getByRole('tab', { name: 'Report' }).click();
  await expect(page.getByText('Written under report/ by fse report.')).toBeVisible();
  // test 5's run keeps no report: no Report tab.
  await page.goto('/runs/backtest-e2e');
  await expect(page.getByText('They are not a forecast of future results.')).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Report' })).toHaveCount(0);
});

test('8. analytics: cards, equity, trades, live updates, daily check and the Nexus block', async ({}, testInfo) => {
  await saveAccountsRevision();
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/live');
  // D-1: from the sidebar, under Monitor
  const monitor = page.locator('aside.shell__nav');
  await monitor.getByRole('button', { name: 'Analytics' }).click();
  await expect(page).toHaveURL(/\/analytics$/);
  await expect(page.getByRole('heading', { name: 'Analytics' })).toBeVisible();
  const total = panel(/^\s*Total/);
  const account = panel(/^\s*topstep_a/);
  const plugin_a = panel(/^\s*plugin_a/);
  const second = panel(/^\s*plugin_b/);
  for (const card of [total, account, plugin_a, plugin_b]) await expect(card).toBeVisible();
  // e2e_server.py: plugin_a closed 13 trades today for +$8.88; its open 2-lot is valued at
  // the live mark of marks.json (quotes 80,010-80,012 ticks: +$7.52 to +$9.52), the Live
  // page's own live P&L
  await expect(stat(first, 'Today net')).toHaveText('+$8.88');
  await expect(stat(first, 'Open P&L')).toHaveText(/^\+\$[79]\.\d{2}$|^\+\$8\.\d{2}$/);
  await expect(stat(first, 'Trades today')).toHaveText('13');
  await expect(stat(second, 'Today net')).toHaveText(/^[+−]?\$\d/);
  await expect(stat(total, 'Cumulative net')).toHaveText(/^[+−]?\$\d/);
  await expect(account.locator('.fse-panel__actions')).toContainText('Active');
  await expect(kv(account, 'Balance')).toHaveText('$50,090.40');
  await expect(account.getByRole('region', { name: 'Distance to limits' })).toContainText('MLL');
  await expect(page.getByRole('img', { name: 'Equity curve in dollars' })).toBeVisible();
  const trades = panel('Trades');
  await expect(trades.getByRole('columnheader', { name: 'Exit reason' })).toBeVisible();
  await expect(trades.locator('tbody tr')).toHaveCount(80);
  await page.screenshot({ path: screenshotPath(testInfo, 'analytics-1280x800.png') });

  // D-2: a book change reaches the card over the WebSocket, no reload
  rewrite(path.join(dataDir(), 'live-state', 'plugins', 'plugin_a', 'plugin.json'),
    (t) => t.replace(/"day_net":\{"\$decimal":"[^"]*"\}/g, '"day_net":{"$decimal":"-120.00"}'));
  await expect(stat(first, 'Today net')).toHaveText('−$120.00', { timeout: 6000 });

  // D-3: the day picker lists a full and a .partial day
  const daily = page.getByRole('region', { name: 'Daily check' });
  await daily.getByRole('button', { name: '2026-10-12' }).click();
  const full = panel(/^\s*2026-10-12$/);
  await expect(full.getByRole('region', { name: 'Cumulative since forward-test start' })).toBeVisible();
  await expect(full.getByText('PARTIAL', { exact: true })).toHaveCount(0);
  await expect(kv(full, 'Overall')).toHaveText('OK (exit 0)');
  const partialDay = daily.getByRole('button', { name: /2026-10-13/ });
  await expect(partialDay).toContainText('PARTIAL');
  await partialDay.click();
  const partial = panel(/^\s*2026-10-13\.partial/);
  await expect(partial.locator('.fse-panel__actions')).toHaveText('PARTIAL');
  const cumulative = partial.getByRole('region', { name: 'Cumulative since forward-test start' });
  await expect(cumulative).toContainText('(so far)');
  // the per-plugin table first, then the per-account one
  await expect(cumulative.getByRole('table').first().getByRole('row').filter({ hasText: 'plugin_a' })).toContainText('+$41.26');
  await expect(cumulative.getByRole('table').nth(1).getByRole('row').filter({ hasText: 'topstep_a' })).toContainText('active');

  // H-10: no Nexus block with the mirror off; shown once the mirror writes its status
  const nexus = panel(/^\s*Nexus mirror/);
  await expect(nexus).toHaveCount(0);
  const status = {
    version: 1, account: 'topstep_a', enabled: true, disabled: false, disabled_reason: null, disabled_by: null,
    nexus_account: 'practice', test_only: true, queue_depth: 2, last_success_ns: Date.now() * 1e6, last_error: null,
    opens_blocked: false, opens_blocked_day: null, ended: false, outbox_degraded: false, local_net: '90.40', nexus_net: '88.15',
  };
  fs.writeFileSync(path.join(dataDir(), 'live-state', 'accounts', 'topstep_a', 'nexus_status.json'), JSON.stringify(status));
  await expect(nexus).toBeVisible({ timeout: 6000 });
  await expect(kv(nexus, 'Mirror')).toHaveText('on');
  await expect(kv(nexus, 'Queue depth')).toHaveText('2');
  await expect(kv(nexus, 'Gap (local − Nexus)')).toHaveText('+$2.25');

  await page.locator('main.shell__main').evaluate((m) => { m.scrollTop = 0; });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('heading', { name: 'Analytics' })).toBeVisible();
  await page.screenshot({ path: screenshotPath(testInfo, 'analytics-390x844.png') });
  await page.setViewportSize({ width: 1280, height: 800 });
});

test('9. plugins: assign an account, edit an override, save; the status bar follows the accounts', async ({}, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('/plugins');
  const statusbar = page.locator('footer.statusbar');
  await expect(statusbar).toContainText('Paper · 1 accounts');
  // the beforehand revision put plugin_a on topstep_a
  await expect(panel(/^\s*plugin_a/).getByLabel('Account')).toHaveValue('topstep_a');
  const second = panel(/^\s*plugin_b/);
  await second.getByLabel('Account').selectOption('topstep_a');
  const accounts = panel('Paper accounts');
  const row = accounts.locator('.row', { hasText: 'topstep_a' });
  await expect(row).toContainText('used by plugin_a, plugin_b');
  await row.getByRole('button', { name: 'Edit' }).click();
  const editor = panel('Edit topstep_a');
  const balance = editor.getByLabel('Starting balance');
  await expect(balance).toHaveAttribute('placeholder', '50000.00');
  await balance.fill('100000.00');
  await editor.scrollIntoViewIfNeeded();
  await page.screenshot({ path: screenshotPath(testInfo, 'plugins-accounts-1280x800.png') });
  await editor.getByRole('button', { name: 'Apply to YAML' }).click();
  await expect(editor).toHaveCount(0);
  await page.getByRole('button', { name: 'Save revision' }).click();
  await expect(page.getByRole('status').filter({ hasText: 'Saved runner' })).toBeVisible();

  await page.reload();
  await expect(panel(/^\s*plugin_b/).getByLabel('Account')).toHaveValue('topstep_a');
  // one plugin card holds both the account select and main's Publish Finding Cards switch
  const firstCard = panel(/^\s*plugin_a/);
  await expect(firstCard.getByRole('switch', { name: 'Publish Finding Cards' })).toBeChecked();
  await firstCard.getByLabel('Account').evaluate((el) => el.scrollIntoView({ block: 'start' }));
  await page.locator('main.shell__main').evaluate((m) => { m.scrollTop -= 120; });
  await page.screenshot({ path: screenshotPath(testInfo, 'plugins-card-1280x800.png') });
  await firstCard.getByRole('switch', { name: 'Publish Finding Cards' }).evaluate((el) => el.scrollIntoView({ block: 'center' }));
  await page.screenshot({ path: screenshotPath(testInfo, 'plugins-cards-switch-1280x800.png') });
  const saved = parse(await runnerYaml());
  expect(saved.plugins.find((p: { id: string }) => p.id === 'plugin_b').account).toBe('topstep_a');
  expect(saved.plugins.find((p: { id: string }) => p.id === 'plugin_a').account).toBe('topstep_a');
  // only the override is written under rules (the preset-diff writer)
  expect(saved.accounts).toEqual([
    { id: 'topstep_a', preset: 'topstep_50k_combine', rules: { starting_balance: { value: '100000.00' } } },
  ]);

  // A-10: the status bar label follows the account state
  rewrite(path.join(dataDir(), 'live-state', 'accounts', 'topstep_a', 'account.json'),
    (t) => t.replace('"status":"active"', '"status":"breached"'));
  await expect(statusbar).toContainText('Paper · 1 breached', { timeout: 6000 });
});

test('12. live: open P&L at the live mark, plan and runner log newest first', async () => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  await page.goto('/live');
  const card = panel('E2E live P&L');
  await expect(card).toBeVisible();
  // long 2 MNQ at 20,000.00 valued at a quote the fixture rewrites every second
  await expect(card.getByTestId('open-pnl-e2e-1')).toHaveText(/^\+\$\d+\.\d{2} \(\+\d+ t\)$/);
  await expect(card.getByTestId('open-pnl-e2e-1')).toHaveClass(/text-bull/);
  await expect(card.getByTestId('today-line')).toContainText(/closed −\$12\.48 \+ open \$\d+\.\d{2} = −\$\d+\.\d{2} · 2 entries · 1 closed/);
  await expect(card).toContainText('as of');
  await expect(card.getByText('stale', { exact: true })).toHaveCount(0);
  const plan = card.getByRole('list', { name: 'E2E live P&L schedule' }).getByRole('listitem');
  await expect(plan).toHaveText([/T3: planned/, /T2: entered long/, /T1: entered, stopped out/, /Flat before the close/]);
  // the stub runner prints a few lines and exits: the log reads newest first
  const runner = page.locator('section.fse-panel').filter({ has: page.getByRole('button', { name: 'Start', exact: true }) });
  await runner.getByRole('button', { name: 'Start', exact: true }).click();
  const log = runner.locator('pre.logview > div');
  await expect(log.first()).toHaveText(/^runner exited 0/, { timeout: 15_000 });
  await expect(log.last()).toHaveText(/^\$ /);
  await card.scrollIntoViewIfNeeded();
  await card.screenshot({ path: `${SHOTS}/live-pnl-card.png` });
  await runner.screenshot({ path: `${SHOTS}/live-runner-log.png` });
  await page.screenshot({ path: `${SHOTS}/live-page.png`, fullPage: true });
});

test('13. cards: every plugin\'s decision cards, newest first, filter and raw JSON', async () => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/live');
  await expect(panel('E2E live P&L').getByTestId('open-pnl-e2e-1')).toHaveText(/^\+\$\d+\.\d{2}/);
  await page.goto('/cards');
  await expect(page.getByRole('heading', { name: 'Finding cards' })).toBeVisible();
  const rows = page.locator('.filelist > button');
  // newest first; the T1 result is joined to its entry card, not a row of its own
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText('e2e_live');
  await expect(rows.nth(0)).toContainText('T2: long MNQ');
  await expect(rows.nth(1)).toContainText('e2e_plugin');
  await expect(rows.nth(1)).toContainText('09:45 slot skipped');
  await expect(rows.nth(2)).toContainText('T1: long MNQ');
  await expect(rows.nth(2)).toContainText('result');
  await rows.nth(2).click();
  const detail = panel('T1: long MNQ');
  await expect(detail).toContainText('Price above the EMA at the T1 slot');
  await expect(detail.getByText('−$12.48')).toHaveClass(/text-bear/);
  await expect(detail).not.toContainText('"ref": "e2e-0"');
  await detail.getByRole('button', { name: 'Show raw JSON' }).click();
  await expect(detail).toContainText('"ref": "e2e-0"');
  await page.screenshot({ path: `${SHOTS}/cards-page.png`, fullPage: true });
  await page.getByLabel('Plugin').selectOption('e2e_plugin');
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText('09:45 slot skipped');
  await page.screenshot({ path: `${SHOTS}/cards-filtered.png`, fullPage: true });
});
