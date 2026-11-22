import { defineConfig } from "@playwright/test";
import { DESKTOP_CHROMIUM } from "./tests/playwright-desktop";
// An installation whose first administrator comes from VAULT_SETUP_ADMIN_*, the way
// an app-store form provisions it. VAULT_SETUP_MODE=environment keeps browser
// registration shut, so the flow cannot depend on the trusted-network wizard.
const port = Number(process.env.PLAYWRIGHT_ENVIRONMENT_ADMIN_PORT ?? 3332);
const apiPort = Number(process.env.PLAYWRIGHT_ENVIRONMENT_ADMIN_API_PORT ?? 8432);
const apiBase = `http://127.0.0.1:${apiPort}`;
export default defineConfig({
  testDir: "./tests/e2e-real/environment-admin",
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 20_000 },
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  webServer: [
    {
      command: "bash tests/e2e-real/scripts/start-backend.sh",
      url: `${apiBase}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 120_000,
      env: {
        PLAYWRIGHT_REAL_API_PORT: String(apiPort),
        PLAYWRIGHT_REAL_DATA_DIR: `/tmp/printstash-environment-admin-${apiPort}`,
        VAULT_SETUP_MODE: "environment",
        VAULT_SETUP_ADMIN_USERNAME: "store-owner",
        VAULT_SETUP_ADMIN_PASSWORD: "StoreFormPassword123",
      },
    },
    {
      command: `VITE_API_URL=${apiBase} ./node_modules/.bin/vite --port ${port} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${port}`,
      reuseExistingServer: false,
    },
  ],
  projects: [{ name: "chromium", use: { ...DESKTOP_CHROMIUM } }],
});
