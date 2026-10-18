// Playwright smoke suite (design 13.1). Runs against the built SPA (`npm run build`
// first) served by the real API in dev-auth mode with a stub fse (api/tests/e2e_server.py).
import os from 'node:os';
import path from 'node:path';
import { defineConfig, devices } from '@playwright/test';

const PORT = 18765;
const PYTHON = process.env.E2E_PYTHON ?? '../api/.venv/bin/python';
/** e2e_server.py writes its temp root here; the spec reads it (same path) to edit seeded files. */
const ROOT_FILE = path.join(os.tmpdir(), 'fse-e2e-root.txt');

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    headless: true,
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  webServer: {
    command: `${PYTHON} ../api/tests/e2e_server.py --port ${PORT} --root-file ${ROOT_FILE}`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    // SIGTERM lets uvicorn return, so e2e_server.py removes its temp folder.
    gracefulShutdown: { signal: 'SIGTERM', timeout: 10_000 },
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
