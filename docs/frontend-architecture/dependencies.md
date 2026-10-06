# Dependency usage inventory

Reference: `b0e79c3e8e41a62a4ad1ad7d092b0bc61f7f4b1b`, 2026-10-06. This is a static import/text inventory, not an unused-dependency proof. Type, CSS, build-time and computed imports require review. Declared ranges below are not installed versions. No dependency is removed by this plan.

## frontend/package.json

| Dependency                                           | Declared range | Usage evidence                                                                                                                      |
| ---------------------------------------------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `@dnd-kit/core` (dependencies)                       | `^6.3.1`       | `frontend/src/components/filter-sidebar.tsx`                                                                                        |
| `@dnd-kit/utilities` (dependencies)                  | `^3.2.2`       | No direct text match; inspect tool invocation, peer or transitive use before deciding                                               |
| `@fontsource-variable/inter` (dependencies)          | `^5.3.0`       | `frontend/src/fonts.css`                                                                                                            |
| `@fontsource-variable/jetbrains-mono` (dependencies) | `^5.3.0`       | `frontend/src/fonts.css`                                                                                                            |
| `@noble/hashes` (dependencies)                       | `^2.4.0`       | `frontend/src/lib/artifact-upload.ts`                                                                                               |
| `@printstash/domain` (dependencies)                  | `workspace:*`  | `frontend/packages/domain/package.json`, `frontend/scripts/coverage-gate.mjs`                                                       |
| `@printstash/ui` (dependencies)                      | `workspace:*`  | `frontend/packages/ui/package.json`, `frontend/packages/ui/src/__tests__/index.test.tsx`                                            |
| `@radix-ui/react-icons` (dependencies)               | `^1.3.2`       | No direct text match; inspect tool invocation, peer or transitive use before deciding                                               |
| `@radix-ui/react-separator` (dependencies)           | `^1.1.8`       | No direct text match; inspect tool invocation, peer or transitive use before deciding                                               |
| `@react-three/drei` (dependencies)                   | `^10.7.7`      | `frontend/src/components/gcode-viewer.tsx`, `frontend/src/components/stl-viewer.tsx`                                                |
| `@react-three/fiber` (dependencies)                  | `^9.6.1`       | `frontend/src/components/gcode-viewer.tsx`, `frontend/src/components/stl-viewer.tsx`                                                |
| `@tanstack/react-query` (dependencies)               | `^5.101.0`     | `frontend/src/components/__tests__/fleet-panels.test.tsx`, `frontend/src/components/__tests__/printers-list.test.tsx`               |
| `@tanstack/react-query-devtools` (dependencies)      | `^5.101.0`     | `frontend/src/main.tsx`                                                                                                             |
| `@types/three` (dependencies)                        | `0.182.0`      | `frontend/scripts/viewer-representation-pilot/package.json`                                                                         |
| `lucide-react` (dependencies)                        | `^1.17.0`      | `frontend/packages/ui/package.json`, `frontend/packages/ui/src/components/__tests__/empty-state.test.tsx`                           |
| `pdfjs-dist` (dependencies)                          | `5.4.296`      | `frontend/src/components/pdf-viewer.tsx`                                                                                            |
| `react` (dependencies)                               | `^19.2.7`      | `frontend/.oxlintrc.json`, `frontend/packages/ui/package.json`                                                                      |
| `react-dom` (dependencies)                           | `^19.2.7`      | `frontend/packages/ui/package.json`, `frontend/packages/ui/src/components/drawer.tsx`                                               |
| `react-markdown` (dependencies)                      | `^10.1.0`      | `frontend/src/components/markdown-view.tsx`                                                                                         |
| `react-pdf` (dependencies)                           | `^10.4.1`      | `frontend/src/components/__tests__/pdf-viewer.test.tsx`, `frontend/src/components/pdf-viewer.tsx`                                   |
| `react-router-dom` (dependencies)                    | `^7.17.0`      | `frontend/src/__tests__/router.test.tsx`, `frontend/src/components/__tests__/app-shell.test.tsx`                                    |
| `recharts` (dependencies)                            | `^3.8.1`       | `frontend/src/pages/statistics.tsx`                                                                                                 |
| `rehype-sanitize` (dependencies)                     | `^6.0.0`       | `frontend/src/components/markdown-view.tsx`                                                                                         |
| `remark-gfm` (dependencies)                          | `^4.0.1`       | `frontend/src/components/markdown-view.tsx`                                                                                         |
| `sonner` (dependencies)                              | `^2.0.7`       | `frontend/src/components/__tests__/fleet-panels.test.tsx`, `frontend/src/components/model-detail/viewer-toolbar.tsx`                |
| `three` (dependencies)                               | `0.182.0`      | `frontend/packages/domain/src/__tests__/card-metrics.test.ts`, `frontend/packages/domain/src/__tests__/currency.test.ts`            |
| `three-stdlib` (dependencies)                        | `^2.36.1`      | `frontend/src/components/stl-viewer.tsx`, `frontend/vite.config.ts`                                                                 |
| `@oxlint/plugins` (devDependencies)                  | `1.79.0`       | `frontend/tools/oxlint/anti-slop/effect/index.ts`, `frontend/tools/oxlint/anti-slop/effect/rules/no-service-constructor-imports.ts` |
| `@playwright/test` (devDependencies)                 | `1.60.0`       | `frontend/playwright.ai-search.config.ts`, `frontend/playwright.config.ts`                                                          |
| `@tailwindcss/typography` (devDependencies)          | `^0.5.20`      | `frontend/src/globals.css`                                                                                                          |
| `@tailwindcss/vite` (devDependencies)                | `^4.3.3`       | `frontend/vite.config.ts`                                                                                                           |
| `@testing-library/jest-dom` (devDependencies)        | `^6.9.1`       | `frontend/packages/ui/tsconfig.json`, `frontend/src/components/__tests__/app-shell.test.tsx`                                        |
| `@testing-library/react` (devDependencies)           | `^16.3.2`      | `frontend/packages/ui/src/components/__tests__/badge.test.tsx`, `frontend/packages/ui/src/components/__tests__/button.test.tsx`     |
| `@testing-library/user-event` (devDependencies)      | `^14.6.1`      | `frontend/src/components/__tests__/ai-search-settings.test.tsx`, `frontend/src/components/__tests__/ai-search-setup.test.tsx`       |
| `@types/node` (devDependencies)                      | `^24.12.4`     | No direct text match; inspect tool invocation, peer or transitive use before deciding                                               |
| `@types/react` (devDependencies)                     | `^19.2.16`     | No direct text match; inspect tool invocation, peer or transitive use before deciding                                               |
| `@types/react-dom` (devDependencies)                 | `^19.2.3`      | No direct text match; inspect tool invocation, peer or transitive use before deciding                                               |
| `@vitejs/plugin-react` (devDependencies)             | `^6.1.0`       | `frontend/packages/ui/vitest.config.ts`, `frontend/vite.config.ts`                                                                  |
| `@vitest/coverage-v8` (devDependencies)              | `^4.1.11`      | No direct text match; inspect tool invocation, peer or transitive use before deciding                                               |
| `happy-dom` (devDependencies)                        | `^20.11.6`     | No direct text match; inspect tool invocation, peer or transitive use before deciding                                               |
| `jsdom` (devDependencies)                            | `^29.1.1`      | `frontend/packages/domain/vitest.config.ts`, `frontend/packages/ui/src/components/__tests__/tabs.test.tsx`                          |
| `oxc-parser` (devDependencies)                       | `0.146.0`      | `frontend/tests/repo/i18n-coverage.test.ts`                                                                                         |
| `oxc-transform-react` (devDependencies)              | `^0.145.0`     | No direct text match; inspect tool invocation, peer or transitive use before deciding                                               |
| `oxfmt` (devDependencies)                            | `0.64.0`       | `frontend/.oxfmtrc.json`                                                                                                            |
| `oxlint` (devDependencies)                           | `1.79.0`       | `frontend/.oxfmtrc.json`, `frontend/.oxlintrc.json`                                                                                 |
| `tailwindcss` (devDependencies)                      | `^4.3.3`       | `frontend/packages/ui/tailwind-preset.cjs`, `frontend/src/globals.css`                                                              |
| `typescript` (devDependencies)                       | `^7.0.2`       | `frontend/.oxlintrc.json`, `frontend/src/components/upload-modal.tsx`                                                               |
| `vite` (devDependencies)                             | `^8.2.2`       | `frontend/.oxlintrc.json`, `frontend/packages/domain/package.json`                                                                  |
| `vitest` (devDependencies)                           | `^4.1.11`      | `frontend/.oxlintrc.json`, `frontend/packages/domain/package.json`                                                                  |

## frontend/packages/ui/package.json

| Dependency                                | Declared range | Usage evidence                                                                                                                  |
| ----------------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| `@radix-ui/react-slot` (dependencies)     | `^1.2.4`       | `frontend/packages/ui/src/components/button.tsx`                                                                                |
| `class-variance-authority` (dependencies) | `^0.7.1`       | `frontend/packages/ui/src/components/badge.tsx`, `frontend/packages/ui/src/components/button.tsx`                               |
| `clsx` (dependencies)                     | `^2.1.1`       | `frontend/packages/ui/src/lib/__tests__/utils.test.ts`, `frontend/packages/ui/src/lib/utils.ts`                                 |
| `lucide-react` (dependencies)             | `^1.17.0`      | `frontend/packages/ui/src/components/__tests__/empty-state.test.tsx`, `frontend/packages/ui/src/components/button.tsx`          |
| `tailwind-merge` (dependencies)           | `^3.6.0`       | `frontend/packages/ui/src/lib/__tests__/utils.test.ts`, `frontend/packages/ui/src/lib/utils.ts`                                 |
| `react` (peerDependencies)                | `>=18`         | `frontend/packages/ui/src/components/__tests__/badge.test.tsx`, `frontend/packages/ui/src/components/__tests__/button.test.tsx` |
| `react-dom` (peerDependencies)            | `>=18`         | `frontend/packages/ui/src/components/drawer.tsx`, `frontend/packages/ui/src/components/modal.tsx`                               |

## frontend/packages/domain/package.json

| Dependency | Declared range | Usage evidence |
| ---------- | -------------- | -------------- |

## browser-extension/package.json

| Dependency                                    | Declared range | Usage evidence                                                                                                |
| --------------------------------------------- | -------------- | ------------------------------------------------------------------------------------------------------------- |
| `@types/ws` (devDependencies)                 | `^8.18.1`      | No direct text match; inspect tool invocation, peer or transitive use before deciding                         |
| `@wdio/cli` (devDependencies)                 | `^9.24.0`      | No direct text match; inspect tool invocation, peer or transitive use before deciding                         |
| `@wdio/local-runner` (devDependencies)        | `^9.24.0`      | No direct text match; inspect tool invocation, peer or transitive use before deciding                         |
| `@wdio/mocha-framework` (devDependencies)     | `^9.24.0`      | No direct text match; inspect tool invocation, peer or transitive use before deciding                         |
| `@webext-core/fake-browser` (devDependencies) | `^1.3.0`       | `browser-extension/tests/e2e/loaded-extension.e2e.ts`, `browser-extension/tests/popup.test.ts`                |
| `fflate` (devDependencies)                    | `0.8.2`        | `browser-extension/tests/store/package.store.ts`                                                              |
| `jsdom` (devDependencies)                     | `^29.0.1`      | `browser-extension/vitest.config.ts`, `browser-extension/vitest.store.config.ts`                              |
| `oxfmt` (devDependencies)                     | `0.64.0`       | `browser-extension/.oxfmtrc.json`                                                                             |
| `oxlint` (devDependencies)                    | `1.79.0`       | `browser-extension/capture-adapter.ts`, `browser-extension/core.ts`                                           |
| `typescript` (devDependencies)                | `^7.0.2`       | No direct text match; inspect tool invocation, peer or transitive use before deciding                         |
| `vitest` (devDependencies)                    | `^4.1.11`      | `browser-extension/tests/browser-provider-adapter.test.ts`, `browser-extension/tests/capture-adapter.test.ts` |
| `ws` (devDependencies)                        | `^8.21.3`      | `browser-extension/browser-provider-adapter.ts`, `browser-extension/capture-adapter.ts`                       |
| `wxt` (devDependencies)                       | `^0.21.4`      | `browser-extension/tsconfig.json`, `browser-extension/vitest.config.ts`                                       |
