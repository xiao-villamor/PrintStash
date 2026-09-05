import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.PLAYWRIGHT_CRITICAL_BACKUP_PORT ?? 3321);
const apiPort = Number(process.env.PLAYWRIGHT_CRITICAL_BACKUP_API_PORT ?? 8421);
const webdavPort = Number(process.env.PLAYWRIGHT_CRITICAL_BACKUP_WEBDAV_PORT ?? 8776);
const apiBase = `http://127.0.0.1:${apiPort}`;

export default defineConfig({
  testDir: "./tests/e2e-real/critical",
  timeout: 120_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: 1,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  webServer: [
    {
      command: "bash tests/e2e-real/scripts/start-storage-backend.sh",
      url: `${apiBase}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        PLAYWRIGHT_STORAGE_API_PORT: String(apiPort),
        PLAYWRIGHT_STORAGE_WEBDAV_PORT: String(webdavPort),
        VAULT_SETUP_MODE: "trusted_network",
      },
    },
    {
      command: `VITE_API_URL=${apiBase} ./node_modules/.bin/vite --port ${port} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${port}`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
