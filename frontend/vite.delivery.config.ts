import { defineConfig, mergeConfig } from "vite";
import base from "./vite.config";

// Keep provider browser tests independent of other Vite runners in worktrees.
export default defineConfig(async (environment) =>
  mergeConfig(await base(environment), { cacheDir: "node_modules/.vite-delivery" }),
);
