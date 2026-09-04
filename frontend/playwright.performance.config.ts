import { defineConfig, devices } from "@playwright/test";

const port = Number(process.env.PERF_PORT ?? 3220);
const apiPort = Number(process.env.PERF_API_PORT ?? 4220);
const buildMode = process.env.PERF_BUILD_MODE === "react-compiler" ? "react-compiler" : "baseline";
const buildCommand = buildMode === "react-compiler" ? "pnpm build:react-compiler" : "pnpm build";

export default defineConfig({
  testDir: "./tests/performance",
  outputDir: `test-results/performance-${buildMode}`,
  timeout: 60_000,
  fullyParallel: false,
  workers: 1,
  repeatEach: 3,
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    trace: "off",
  },
  webServer: {
    command: `${buildCommand} && pnpm exec vite preview --port ${port} --strictPort --host 127.0.0.1`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 180_000,
    env: {
      VITE_API_URL: `http://127.0.0.1:${apiPort}`,
    },
  },
  projects: [
    {
      name: `chromium-${buildMode}`,
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
