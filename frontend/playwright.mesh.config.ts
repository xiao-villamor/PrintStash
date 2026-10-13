/** Native renderer integration checks, including deliberate boundary failures. */
import { defineConfig } from "@playwright/test";
import base from "./playwright.config";

export default defineConfig(base, {
  testDir: "./tests/browser",
  outputDir: "test-results/mesh",
});
