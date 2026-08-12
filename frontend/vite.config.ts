/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

const API_TARGET = process.env.VITE_API_URL || "http://localhost:8000";

// Client SPA. Dev-server proxy replaces the old next.config rewrites; in prod
// the built `dist/` is served behind the same reverse proxy as the API.
export default defineConfig({
  plugins: [react()],
  resolve: {
    tsconfigPaths: true,
  },
  // The 3D/G-code viewers (`stl-viewer`, `gcode-viewer`) are lazy-loaded, so
  // their heavy `three` stack isn't reachable from the initial entry. Without
  // this, Vite only discovers these deps the *first* time a model is opened,
  // then pre-bundles ~2 MB of `drei` + `three` with esbuild and forces a full
  // reload — a one-off ~30s stall on the first viewer open (worse on WSL2).
  // Listing them here pre-bundles at dev-server startup instead.
  optimizeDeps: {
    include: [
      "three",
      "three-stdlib",
      "@react-three/fiber",
      "@react-three/drei",
    ],
  },
  server: {
    port: 3000,
    proxy: {
      "/api/v1": {
        target: API_TARGET,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
  test: {
    // Unit tests run in jsdom (localStorage, window) and exclude the Playwright
    // e2e suite, which has its own runner.
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["tests/e2e/**", "node_modules/**"],
    coverage: {
      provider: "v8",
      reporter: ["text", "html"],
      include: ["src/lib/**/*.{ts,tsx}"],
      exclude: ["src/**/*.{test,spec}.{ts,tsx}", "src/**/*.d.ts"],
      // Informative floor set just under the current baseline (measured
      // 2026-07: stmts 52.9 / branches 50.9 / funcs 43.5 / lines 54.6). It
      // guards against regressions today; ratchet these up toward the real
      // gate as `src/lib` coverage grows.
      thresholds: {
        statements: 50,
        branches: 48,
        functions: 40,
        lines: 52,
      },
    },
  },
});
