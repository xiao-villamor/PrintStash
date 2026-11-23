import { defineConfig } from "@playwright/test";
import { DESKTOP_CHROMIUM } from "./tests/playwright-desktop";

// farm_browser.py starts the bundled app and owns its loopback URL and native devices.
export default defineConfig({
  testDir: "./tests/e2e-real/farm-lab",
  workers: 1,
  retries: 0,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  reporter: "list",
  outputDir: process.env.FARM_BROWSER_OUTPUT,
  projects: [{ name: "chromium", use: { ...DESKTOP_CHROMIUM } }],
  use: { baseURL: process.env.FARM_BROWSER_URL, trace: "off" },
});
