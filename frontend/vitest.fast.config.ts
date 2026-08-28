import { defineConfig } from "vitest/config";

// A deliberately small, audited lane for pure tests.
//
// These files share a worker (`isolate: false`), which is only safe because none
// of them touches DOM globals, module mocks, fake timers, spies, or any other
// process-global mutable state. One file that does would corrupt the others in
// ways that surface as an unrelated failure somewhere else in the lane — the
// worst kind of flake to chase — so membership is audited, not assumed.
//
// The authoritative suite stays isolated in `vite.config.ts`. To add a file here:
// confirm it matches none of `vi.stubGlobal`, `vi.mock`, `vi.spyOn`,
// `vi.useFakeTimers`, `document.`, `window.`, `localStorage`, or `globalThis`,
// then validate it under repeat + shuffle before committing.
//
// Audited 2026-08: every `*.test.ts` in `src/` was re-checked against that list;
// the seven files added below are the ones that passed and were not already here.
// Every `*.test.tsx` renders, so none of them can ever join this lane.
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
      "src/lib/__tests__/branding-assets.test.ts",
      "src/lib/__tests__/bulk-upload.test.ts",
      "src/lib/__tests__/changelog.test.ts",
      "src/lib/__tests__/currency.test.ts",
      "src/lib/__tests__/errors.test.ts",
      "src/lib/__tests__/i18n-coverage.test.ts",
      "src/lib/__tests__/native-dialogs.test.ts",
      "src/lib/__tests__/orca-printer-images.test.ts",
      "src/lib/__tests__/printer-provider-contract.test.ts",
      "src/lib/__tests__/printer-providers.test.ts",
    ],
  },
});
