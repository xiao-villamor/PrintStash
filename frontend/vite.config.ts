import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

const API_TARGET = process.env.VITE_API_URL || "http://localhost:8000";

// Client SPA. Dev-server proxy replaces the old next.config rewrites; in prod
// the built `dist/` is served behind the same reverse proxy as the API.
export default defineConfig(({ mode }) => ({
  plugins: [
    tailwindcss(),
    // Keep the default Oxc transform path fast. Native React Compiler remains
    // an explicit profiling build until it demonstrates an interaction-time
    // win and its unsupported diagnostics have been triaged.
    react({ compiler: mode === "react-compiler" }),
  ],
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
    include: ["three", "three-stdlib", "@react-three/fiber", "@react-three/drei"],
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
    // suites, which have their own runner.
    //
    // `tests/repo/` is the frontend's `backend/tests/repo/`: invariants over the
    // repository itself — the suite's own shape, translation coverage, the ban on
    // native dialogs — which mirror no production module and so have no home under
    // `src/`. It is listed explicitly rather than by widening to `tests/**`, which
    // would swallow the Playwright specs.
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}", "tests/repo/**/*.{test,spec}.{ts,tsx}"],
    exclude: ["tests/e2e/**", "tests/e2e-real/**", "tests/performance/**", "node_modules/**"],
    coverage: {
      provider: "v8",
      // `json-summary` is what scripts/coverage-gate.mjs reads; `html` is what you
      // read. `text` keeps the number in the terminal where the run happened.
      reporter: ["text", "html", "json-summary"],
      reportsDirectory: "coverage",
      // All of `src/`, not just `src/lib/`. The old include was `src/lib/**`, which
      // measured 1,655 of the 7,900 statements this app ships and reported 86% —
      // a number about 21% of the code. Widening it does not make the app worse
      // tested; it makes the gap visible, which is the only way it gets closed.
      //
      // `packages/**` is excluded because its `src/` directories match `src/**` and
      // each workspace package runs its own suite: measured from here they look
      // half-covered purely because the app imports some of them.
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "packages/**",
        "src/**/*.{test,spec}.{ts,tsx}",
        "src/**/__tests__/**",
        "src/**/*.d.ts",
        // Generated from the backend contract; its source of truth is the generator.
        "src/generated/**",
        // Helpers that exist only for tests.
        "src/test-support/**",
        // The bootstrap. Running it *is* starting the app, so a unit test of it
        // would assert that `createRoot` was called.
        "src/main.tsx",
      ],
      // Floors live in scripts/coverage-gate.mjs, not here: vitest thresholds can
      // only enforce a lower bound, and a floor nobody is forced to raise stops
      // being a gate. That script ratchets in both directions and carries a floor
      // per area of the tree.
    },
  },
}));
