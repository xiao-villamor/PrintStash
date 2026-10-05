/** The immutable delivery proof prepares its helper before browser interaction. */
// @vitest-environment node
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { resolveConfig } from "vite";

const FRONTEND_ROOT = resolve(__dirname, "../..");

describe("delivery dependency preparation", () => {
  it("prepares the authenticated helper's query dependency", async () => {
    const config = await resolveConfig(
      { root: FRONTEND_ROOT, configFile: resolve(FRONTEND_ROOT, "vite.delivery.config.ts") },
      "serve",
    );

    expect(config.optimizeDeps.include).toContain("@tanstack/react-query");
    expect(config.optimizeDeps.include).toContain("react");
  });

  it("keeps its prepared graph fixed during downloads", async () => {
    const config = await resolveConfig(
      { root: FRONTEND_ROOT, configFile: resolve(FRONTEND_ROOT, "vite.delivery.config.ts") },
      "serve",
    );

    expect(config.optimizeDeps.noDiscovery).toBe(true);
  });
});
