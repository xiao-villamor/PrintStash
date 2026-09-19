import { defineConfig } from "@playwright/test";
import { resolve } from "node:path";
import real from "./playwright.real.config";

// Exercise opt-in previews against an isolated real backend. The ordinary
// suite uses background previews; neither scenario depends on render timing.
const servers = Array.isArray(real.webServer) ? real.webServer : [real.webServer!];
export default defineConfig({
  ...real,
  testDir: "./tests/e2e-real/enrichment",
  testIgnore: [],
  webServer: servers.map((server, index) => {
    const configured = { ...server, reuseExistingServer: false };
    if (index !== 0) return configured;
    return {
      ...configured,
      timeout: 600_000,
      env: {
        ...server.env,
        VAULT_THUMBNAIL_PROCESSING: "on_demand",
        PLAYWRIGHT_REAL_DATA_DIR: resolve("tests/e2e-real/.data-enrichment"),
      },
    };
  }),
});
