import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.PLAYWRIGHT_REAL_PORT ?? 3310);
const apiPort = Number(process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410);
const api = `http://127.0.0.1:${apiPort}`;

export default defineConfig({
  testDir: "./tests/e2e-real/ai-search",
  testMatch: process.env.PLAYWRIGHT_AI_SEARCH_SPARSE_STUDY ? "**/sparse.preplaced.ts" : undefined,
  timeout: 120000,
  expect: { timeout: 15000 },
  workers: 1,
  retries: 0,
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  webServer: [
    {
      command: "bash tests/e2e-real/scripts/start-ai-search-backend.sh",
      url: `${api}/api/v1/health`,
      reuseExistingServer: false,
      timeout: 120000,
      env: { PLAYWRIGHT_REAL_API_PORT: String(apiPort), VAULT_SETUP_MODE: "trusted_network" },
    },
    {
      command: `VITE_API_URL=${api} ./node_modules/.bin/vite --port ${port} --strictPort --host 127.0.0.1`,
      url: `http://127.0.0.1:${port}`,
      reuseExistingServer: false,
      timeout: 120000,
    },
  ],
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
