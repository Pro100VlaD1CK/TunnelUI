import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e', fullyParallel: false, workers: 1, timeout: 30_000,
  use: { baseURL: 'http://127.0.0.1:8765', channel: 'chrome', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: {
    command: process.platform === 'win32' ? '..\\.venv\\Scripts\\python.exe ..\\scripts\\e2e_server.py' : '../.venv/bin/python ../scripts/e2e_server.py',
    url: 'http://127.0.0.1:8765/api/health', reuseExistingServer: false, timeout: 30_000,
  },
});
