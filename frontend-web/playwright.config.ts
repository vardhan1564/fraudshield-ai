import { defineConfig, devices } from "@playwright/test";

// Default: the UI on the MSW mocks (`npm run dev:mock`), so e2e needs no backend.
// Real stack: E2E_BASE_URL=http://localhost:5173 E2E_EMAIL=... E2E_PASSWORD=... npx playwright test
const REAL = process.env.E2E_BASE_URL;
const MOCK_PORT = 5174;

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: REAL ?? `http://localhost:${MOCK_PORT}`,
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  // Locally use the installed Chrome (no browser download); CI installs Playwright's Chromium.
  projects: [{ name: "chrome", use: { ...devices["Desktop Chrome"], channel: process.env.CI ? undefined : "chrome" } }],
  webServer: REAL
    ? undefined
    : {
        command: `npm run dev:mock -- --port ${MOCK_PORT} --strictPort`,
        url: `http://localhost:${MOCK_PORT}`,
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      },
});
