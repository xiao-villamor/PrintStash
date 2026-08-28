import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["../../vitest.setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
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
