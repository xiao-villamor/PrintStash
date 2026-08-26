import { defineConfig } from "vitest/config";

// A deliberately small, audited lane for pure tests. These files do not use
// DOM globals, module mocks, fake timers, or process-global mutable state, so
// they can share a worker safely. Keep the authoritative suite isolated in
// vite.config.ts; add files here only after repeat + shuffle validation.
export default defineConfig({
  resolve: {
    tsconfigPaths: true,
  },
  test: {
    globals: true,
    environment: "node",
    pool: "threads",
    isolate: false,
    include: [
      "src/components/model-detail/__tests__/presentation.test.ts",
      "src/generated/__tests__/printer-contracts.test.ts",
      "src/lib/__tests__/currency.test.ts",
      "src/lib/__tests__/errors.test.ts",
      "src/lib/__tests__/printer-providers.test.ts",
    ],
  },
});
