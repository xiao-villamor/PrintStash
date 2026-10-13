import { defineConfig, mergeConfig } from "vite";
import base from "./vite.config.ts";

// Keep provider browser tests independent of other Vite runners in worktrees.
export default defineConfig(async (environment) =>
  mergeConfig(await base(environment), {
    cacheDir: "node_modules/.vite-delivery",
    optimizeDeps: {
      entries: ["tests/e2e-real/delivery/harness.html"],
      // This immutable proof has one dependency graph. A late discovery can
      // invalidate the document while its authenticated fetch is in flight.
      include: ["@tanstack/react-query"],
      noDiscovery: true,
    },
  }),
);
