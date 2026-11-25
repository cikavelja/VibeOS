import { existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { defineConfig } from '@playwright/test';

const port = Number(process.env.PLAYWRIGHT_PORT || 4173);
const apiPort = Number(process.env.PLAYWRIGHT_API_PORT || 8010);
const baseURL = `http://127.0.0.1:${port}`;
const apiTarget = `http://127.0.0.1:${apiPort}`;
const e2eDbPath = resolve(tmpdir(), `vibeos-playwright-${process.pid}.db`).replace(/\\/g, '/');

function resolveBackendPython(): string {
  if (process.env.PYTHON) return process.env.PYTHON;
  const winVenv = resolve('..', 'backend', '.venv', 'Scripts', 'python.exe');
  if (existsSync(winVenv)) return `"${winVenv}"`;
  const posixVenv = resolve('..', 'backend', '.venv', 'bin', 'python');
  if (existsSync(posixVenv)) return `"${posixVenv}"`;
  return process.platform === 'win32' ? 'python' : 'python3';
}

export default defineConfig({
  testDir: '../tests/e2e',
  use: { baseURL },
  webServer: [
    {
      command: `${resolveBackendPython()} -m uvicorn app.main:app --host 127.0.0.1 --port ${apiPort}`,
      cwd: '../backend',
      url: `${apiTarget}/api/v1/health`,
      env: {
        ...process.env,
        DATABASE_URL: `sqlite:///${e2eDbPath}`,
      },
      reuseExistingServer: false,
    },
    {
      command: `npx vite --host 127.0.0.1 --port ${port} --strictPort`,
      url: baseURL,
      env: {
        ...process.env,
        API_TARGET: apiTarget,
      },
      reuseExistingServer: false,
    },
  ],
});
