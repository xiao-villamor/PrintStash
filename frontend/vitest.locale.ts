import { ensureLocaleCatalog, SUPPORTED_LOCALES } from "./src/lib/locale";

// The app loads one catalog before its first render (`main.tsx`). A unit test
// renders components directly and switches locale mid-test, so every catalog is
// loaded up front; `src/lib/__tests__/locale.test.ts` covers the loading itself.
// This is the application's setup only: the workspace packages share
// `vitest.setup.ts` and know nothing of the app's locales.
await Promise.all(SUPPORTED_LOCALES.map(ensureLocaleCatalog));
