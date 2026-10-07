import { defineConfig, devices } from "@playwright/test";
import { resolve } from "node:path";
const port = Number(process.env.STARTUP_PORT ?? 3420);
const apiPort = Number(process.env.STARTUP_API_PORT ?? 8420);
const source = resolve(process.env.STARTUP_FRONTEND_DIR ?? ".");
export default defineConfig({
  testDir: "./tests/performance",
  testMatch: [
    "library-startup.spec.ts",
    "startup-behaviour.spec.ts",
    ...(process.env.STARTUP_DISTRIBUTION === "dense" ? ["startup-resources.spec.ts"] : []),
  ],
  outputDir: ".startup-results/browser",
  timeout: 300_000,
  workers: 1,
  fullyParallel: false,
  reporter: "list",
  use: { baseURL: `http://127.0.0.1:${port}`, trace: "retain-on-failure" },
  webServer: [
    {
      command: "bash tests/performance/scripts/start-backend.sh",
      url: `http://127.0.0.1:${apiPort}/api/v1/health`,
      timeout: 120_000,
      reuseExistingServer: false,
    },
    {
      command: `pnpm exec vite build && bash "${resolve("tests/performance/scripts/start-frontend.sh")}"`,
      cwd: source,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
      url: `http://127.0.0.1:${port}`,
      timeout: 180_000,
      reuseExistingServer: false,
      env: { STARTUP_FRONTEND_DIR: source },
    },
  ],
  projects: [
    {
      name: "chromium-startup",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
  ],
});
