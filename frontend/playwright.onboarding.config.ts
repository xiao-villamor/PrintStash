import { defineConfig, devices } from "@playwright/test";
const port = Number(process.env.PLAYWRIGHT_ONBOARDING_PORT ?? 3331);
const apiPort = Number(process.env.PLAYWRIGHT_ONBOARDING_API_PORT ?? 8431);
const apiBase = `http://127.0.0.1:${apiPort}`;
export default defineConfig({
  testDir: "./tests/e2e-real/onboarding",
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
        PLAYWRIGHT_REAL_DATA_DIR: `/tmp/printstash-onboarding-${apiPort}`,
        VAULT_SETUP_MODE: "trusted_network",
      },
    },
    {
      command: `VITE_API_URL=${apiBase} ./node_modules/.bin/vite --port ${port} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${port}`,
      reuseExistingServer: false,
    },
  ],
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
