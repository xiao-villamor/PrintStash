import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";

const port = Number(process.env.PLAYWRIGHT_DELIVERY_PORT ?? 3335);
const apiPort = Number(process.env.PLAYWRIGHT_DELIVERY_API_PORT ?? 8435);
const origin = `http://127.0.0.1:${port}`;
const apiBase = `http://127.0.0.1:${apiPort}`;
const dataRoot = resolve(
  process.env.PLAYWRIGHT_DELIVERY_DATA_DIR ?? "tests/e2e-real/.delivery-data",
);
process.env.PLAYWRIGHT_DELIVERY_DATA_DIR = dataRoot;

export default defineConfig({
  testDir: "./tests/e2e-real/delivery",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: origin,
    // Only the fixture's test-owned TLS certificate is untrusted. CORS remains
    // enforced by Chromium; this does not disable its web security checks.
    ignoreHTTPSErrors: true,
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "bash tests/e2e-real/scripts/start-delivery-backend.sh",
      url: `${apiBase}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 240_000,
      env: {
        PLAYWRIGHT_DELIVERY_DATA_DIR: dataRoot,
        PLAYWRIGHT_DELIVERY_API_PORT: String(apiPort),
        PLAYWRIGHT_DELIVERY_ORIGIN: origin,
      },
    },
    {
      command: `VITE_API_URL=${apiBase} ./node_modules/.bin/vite --config vite.delivery.config.ts --port ${port} --strictPort --host 127.0.0.1`,
      url: origin,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
