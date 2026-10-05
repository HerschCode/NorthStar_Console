import { defineConfig } from '@playwright/test'

// `fixtures` runs against recorded P1 data and an in-test mock gateway (CI-friendly, no services needed).
// `live` runs the same demo story against the real local stack (scripts/dev.ps1). `screenshots` regenerates docs/screenshots.
// Browser: the installed Microsoft Edge by default (no browser download); PW_CHANNEL=chrome|chromium to change.
const channel = process.env.PW_CHANNEL ?? 'msedge'

export default defineConfig({
  testDir: 'e2e',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5173',
    channel: channel === 'chromium' ? undefined : channel,
    trace: 'retain-on-failure',
    video: process.env.RECORD_VIDEO ? { mode: 'on', size: { width: 1280, height: 720 } } : 'off',
    viewport: { width: 1280, height: 720 },
  },
  webServer: { command: 'npx vite --host 127.0.0.1 --port 5173', url: 'http://127.0.0.1:5173', reuseExistingServer: true, timeout: 60_000 },
  projects: [
    { name: 'fixtures', testMatch: /fixtures\.spec\.ts/ },
    { name: 'live', testMatch: /live\.spec\.ts/ },
    { name: 'screenshots', testMatch: /screenshots\.spec\.ts/ },
  ],
})
