import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.{test,spec}.ts"],
    coverage: {
      provider: "v8",
      // `json-summary` is what ../../scripts/coverage-gate.mjs reads. Floors live
      // there rather than in `thresholds` here, because thresholds can only enforce
      // a lower bound and this one has to be raised as the suite improves.
      reporter: ["text", "html", "json-summary"],
      reportsDirectory: "coverage",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.{test,spec}.{ts,tsx}", "src/**/__tests__/**", "src/**/*.d.ts"],
    },
  },
});
