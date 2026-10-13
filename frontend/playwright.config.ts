import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.PLAYWRIGHT_PORT ?? 3210);
const apiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? 4210);
const productionPwa = process.env.PLAYWRIGHT_PRODUCTION_PWA === "1";
const apiBase = `http://127.0.0.1:${apiPort}`;
const bundledDevFlag = process.env.PLAYWRIGHT_BUNDLED_DEV ? " --experimental-bundle" : "";
// Both Vite servers carry the existing same-origin API proxy.
const webServerCommand = productionPwa
  ? `VITE_API_URL=${apiBase} pnpm exec vite build && VITE_API_URL=${apiBase} pnpm exec vite preview --port ${port} --strictPort --host 127.0.0.1`
  : `VITE_API_URL=${apiBase} ./node_modules/.bin/vite${bundledDevFlag} --port ${port} --strictPort --host 127.0.0.1`;

export default defineConfig({
  testDir: "./tests/e2e",
  testMatch: productionPwa ? ["**/pwa-production.spec.ts"] : undefined,
  testIgnore: productionPwa ? [] : ["**/pwa-production.spec.ts"],
  outputDir: productionPwa ? "test-results/pwa-production" : "test-results",
  timeout: 30_000,
  expect: {
    timeout: 10_000,
  },
  fullyParallel: !productionPwa,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: process.env.CI
    ? [
        ["github"],
        [
          "html",
          {
            open: "never",
            outputFolder: productionPwa ? "playwright-report/pwa-production" : "playwright-report",
          },
        ],
      ]
    : "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "on-first-retry",
  },
  webServer: {
    command: webServerCommand,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: !productionPwa && !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    {
      name: productionPwa ? "chromium-pwa-production" : "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
