import { defineConfig } from "@playwright/test";
import { DESKTOP_CHROMIUM } from "./tests/playwright-desktop";

/** Opt-in inspection of an existing deployment. Never starts or resets a backend. */
export default defineConfig({
  testDir: "./tests/e2e-real/review",
  outputDir: "../reports/model-detail-review",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  use: {
    // The fixture requires an explicit URL and credentials before any request.
    baseURL: process.env.PRINTSTASH_REVIEW_URL
      ? new URL(process.env.PRINTSTASH_REVIEW_URL).origin
      : undefined,
    trace: "off",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...DESKTOP_CHROMIUM } }],
});
