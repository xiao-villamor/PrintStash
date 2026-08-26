import { defineConfig, devices } from "@playwright/test";

// Real end-to-end config: drives the actual FastAPI backend + a throwaway
// SQLite DB (see tests/e2e-real/scripts/start-backend.sh) through a real Vite
// dev server. Unlike playwright.config.ts (mock API), these tests exercise real
// persistence — create/delete really hits the database.
const port = Number(process.env.PLAYWRIGHT_REAL_PORT ?? 3310);
const apiPort = Number(process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410);
const apiBase = `http://127.0.0.1:${apiPort}`;
// Standalone Moonraker + Spoolman emulator (backend/tests/fakes/mock_printer.py)
// for fleet.spec.ts — a real, live printer with no physical hardware involved.
const mockPrinterPort = Number(process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? 7530);

export default defineConfig({
  testDir: "./tests/e2e-real",
  // Generous: some specs upload a file and wait on real async ingestion.
  timeout: 120_000,
  expect: { timeout: 15_000 },
  // Serial: every test shares one backend DB, so isolation comes from unique
  // names + self-cleanup, not parallel workers.
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "on-first-retry",
  },
  webServer: [
    {
      command: "bash tests/e2e-real/scripts/start-backend.sh",
      url: `${apiBase}/api/v1/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      env: {
        PLAYWRIGHT_REAL_API_PORT: String(apiPort),
        VAULT_SETUP_TOKEN: "playwright-setup-token-123",
      },
    },
    {
      command: `VITE_API_URL=${apiBase} ./node_modules/.bin/vite --port ${port} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${port}`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: "bash tests/e2e-real/scripts/start-mock-printer.sh",
      url: `http://127.0.0.1:${mockPrinterPort}/printer/info`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      env: { PLAYWRIGHT_MOCK_PRINTER_PORT: String(mockPrinterPort) },
    },
  ],
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
