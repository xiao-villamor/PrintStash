import { defineConfig, devices } from "@playwright/test";
import { fileURLToPath } from "node:url";

const port = Number(process.env.PLAYWRIGHT_MIGRATION_PORT ?? 3324);
const apiPort = Number(process.env.PLAYWRIGHT_MIGRATION_API_PORT ?? 8424);
const dataRoot = fileURLToPath(new URL("./tests/e2e-real/.data/migration-suite", import.meta.url));
// The shared authenticated-page fixture reads this same real-backend locator.
process.env.PLAYWRIGHT_REAL_API_PORT = String(apiPort);
process.env.PLAYWRIGHT_REAL_DATA_DIR = dataRoot;

export default defineConfig({
  testDir: "./tests/e2e-real/migration",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: 1,
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  webServer: [
    {
      command: "bash tests/e2e-real/scripts/start-backend.sh",
      url: `http://127.0.0.1:${apiPort}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 240_000,
      env: {
        PLAYWRIGHT_REAL_API_PORT: String(apiPort),
        PLAYWRIGHT_REAL_DATA_DIR: dataRoot,
        VAULT_SETUP_MODE: "trusted_network",
      },
    },
    {
      command: `VITE_API_URL=http://127.0.0.1:${apiPort} ./node_modules/.bin/vite --port ${port} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${port}`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
