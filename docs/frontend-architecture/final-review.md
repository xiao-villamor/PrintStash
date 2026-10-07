# Final integration review record

This record adds exact-file inspection evidence to the historical ledger. It does not upgrade unlisted files to reviewed. Hashes identify inspected bytes; subsequent modifications require reconciliation.

| Path | Inspected SHA-256 | Scope / result |
|---|---|---|
| `browser-extension/capture-operation.ts` | `67b84eb75baea459597ea95e27f207d3b4f83ec56f0d0b291d41839fb97113f3` | Complete file. Extension capture fences immutable connection; help/popup static markup retains existing boundaries. |
| `browser-extension/entrypoints/help/main.ts` | `94c5f8f32dccc2fb6206957c51366cb98d5f95929fb3878ec7a8950d8d67d1bd` | Complete file. Extension capture fences immutable connection; help/popup static markup retains existing boundaries. |
| `browser-extension/entrypoints/help/style.css` | `61e9f21396809b4a3694a8a4832e478f7f9b080d878159ae88f3b64394b54b8a` | Complete file. Extension capture fences immutable connection; help/popup static markup retains existing boundaries. |
| `browser-extension/entrypoints/help/index.html` | `dd278f05e53bee1212fc9f5bb6001f24586b54942bad869940514b24a8bc49dc` | Complete file. Extension capture fences immutable connection; help/popup static markup retains existing boundaries. |
| `browser-extension/entrypoints/popup/index.html` | `f25c570bb334195606ad6a6b2c367529a55509070ac7e18f5d731a256936ab1e` | Complete file. Extension capture fences immutable connection; help/popup static markup retains existing boundaries. |
| `frontend/src/components/spoolman-connect-card.tsx` | `cddfe3d38356bd7743e1e5abd4481107e42c91a36e05a018ecb34dd0de105dfa` | Complete file. Spoolman draft hydration/conflict gap requires correction. |
| `frontend/src/lib/api/spoolman.ts` | `0f5a20830ff8afb481a049571f8d1cb6ef98a247e2061d6d68a37f0a294d0afe` | Complete file. Spoolman draft hydration/conflict gap requires correction. |
| `frontend/src/types/spoolman.ts` | `25d149e6f8aca4475ef711ffba3c26f07d82e0ee0b7b5d1f6bc9c5767d6710f9` | Complete file. Spoolman draft hydration/conflict gap requires correction. |
| `frontend/src/components/__tests__/spoolman-connect-card.test.tsx` | `c53553c26f321881a752b7aa9c9ab2059c269a499d052cd79fe33da84420157c` | Complete file. Spoolman draft hydration/conflict gap requires correction. |
| `frontend/src/lib/api/__tests__/spoolman.test.ts` | `62ab8d9e4772ee3fff6274ef9e96199a23b18715e6c81d9c58bf001ecaa9c461` | Complete file. Spoolman draft hydration/conflict gap requires correction. |
| `backend/app/api/v1/spoolman.py` | `c1d3d2c706c0b4e4e32874b091e21f9d0093bffcf3817fd9efe659e5ba55fcba` | Complete file. Spoolman draft hydration/conflict gap requires correction. |
| `frontend/playwright.startup.config.ts` | `62d5343a18c7abc6a76f733ac3d8fb21ac8420a6db1e83f74bd028fc81d09266` | Complete file. Production measurement observer and owned fixture/proxy lifecycle retained. |
| `frontend/tests/performance/library-startup.spec.ts` | `3f8862433e4627ebeeb0778ad4358efedc185554e823516d29de3e7bab73646f` | Complete file. Production measurement observer and owned fixture/proxy lifecycle retained. |
| `frontend/tests/performance/scripts/start-backend.sh` | `fbfa49fc25a97868ac86edcd02bef3f557d9d8af8a00bdbfd644005d23df6212` | Complete file. Production measurement observer and owned fixture/proxy lifecycle retained. |
| `frontend/tests/performance/scripts/start-frontend.sh` | `42eb7b8a51fb649ac7fe28e61d5c3b9f557339147291f0c19012b6b9df4cdd73` | Complete file. Production measurement observer and owned fixture/proxy lifecycle retained. |

## First integrated CI corrections

The first run on the history-reconciled tree found four frontend failures (two stale Similarity source keys and two outdated outliner factory key expectations), two test-layout violations, and four implicit backend re-exports. Correct the callers/test contracts, not the boundary rules. The production library-source owner already replaced the private Similarity cache in M9.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| I1 | retains an unavailable selected source | Edge | Source feature disabled after selection | Selection retained; dispatch blocked | Frontend unit | ✅ affected checks passed |
| I2 | permits library analysis without external sources | Edge | Sources feature disabled | Library analysis remains available | Frontend unit | ✅ affected checks passed |
| I3 | builds an outliner editing identity | Happy | Fixture edit version | Required epoch/version present | Frontend unit | ✅ affected checks passed |
| I4 | keeps imports on their owning contracts | Error | Application dependency graph | No implicit exports or new exceptions | Repository | ✅ affected checks passed |
| I5 | places tests with their production owners | Edge | Current test files | Valid module names and grouped tests | Repository | ✅ affected checks passed |

Integrated repository corrections: 5,101 repository checks passed. The affected frontend source-key and factory checks passed in the focused selection.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| I6 | follows a library link through client navigation | Happy | click internal link | destination visible; prior entry retained | Frontend unit | ✅ `frontend/src/lib/__tests__/link.test.tsx` (26-case Link/Spoolman selection passed) |
| I7 | replaces the current entry when requested | Edge | replace link | back returns to previous entry | Frontend unit | ✅ `frontend/src/lib/__tests__/link.test.tsx` (26-case Link/Spoolman selection passed) |

The remaining Link shim advertised unsupported Next.js prefetch/scroll options with no callers. Remove these ignored options; retain its actual React Router push/replace interface.

The app coverage run completed with 4,382 passing cases and two 5-second test timeouts in the multi-step currency conflict workflow (412/503). Both cases passed unchanged in isolation (2 passed, 5.49 s test time combined). The test-specific budget is now 15 seconds for the complete rendered Settings/review/two-save sequence; individual UI waits and all assertions remain intact. This is a harness timing correction, not a product correctness fix. Coverage requalification is pending.

## Additional bounded file reads

These files were read in full in the final review. The hashes identify the current read revision, including local corrections described above. Leaf UI composes existing primitives; the small library modules keep local presentation, worker cancellation or explicitly bounded preference/asset responsibilities. DTOs contain no cache ownership. Existing loose backend state strings and optional legacy fields remain contract debt; changing their wire semantics is outside this migration. This is not a claim that every test assertion or every platform was audited.

| Path | Inspected SHA-256 | Scope / result |
|---|---|---|
| `frontend/src/components/auth-banner.tsx` | `0b2227bfb8a099d9a4740abe5c35db3a9b61cc8e6d1015df7c04d04d65164b30` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/components/brand-mark.tsx` | `6fcc3afd14c408a39a30d24f1e31448e37e7d678554b7777240fccf75fa29ae1` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/components/fab.tsx` | `10f69d2415f7f591276011b5651799055012db40ba2625c1ee08ac9bd9a368f3` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/components/import-copy-warning.tsx` | `b652bfe00fa501bd0bf8e70ef5233bf10f34fac9123f2e5e93309faa93c09f38` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/components/locale-toggle.tsx` | `7c9e5e580db1e61488fef0c770652d798fd4fe0086fabd7c003712a1206e4817` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/components/makerworld-connect-card.tsx` | `dca483411b96cdd67242aea12d67d89cd1f466ab4b5f66809ee596873c79a691` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/components/metadata-comparison.tsx` | `f053c31420b90f22fe78f37d45e4becfa10e45c1b0dd4572785b62ac065ce559` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/components/mobile-filter-drawer.tsx` | `1840f18ada3cbb2ca4a0996767c588e2532e9a9631b1f5c012420d4ea2a34b87` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/components/mobile-nav-drawer.tsx` | `4d9d04be47b6cad86706b106ac083a4d0e05f393bf98765cc968a2750910b321` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/components/setup-frame.tsx` | `6a86cd7be216065ee5ed7775a42e75ddd87afc8514fc5c74920d7ff3927adbb8` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/components/setup-unavailable.tsx` | `67f254e70df5216ebe487e2e06612703b110cac19f05e1747416e11845828edd` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/components/staged-input-recovery.tsx` | `660bee48c2d2a87e79f6980bceac884e1a74a66317cb765f7c69b950911fc630` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/components/storage-provider-guidance.tsx` | `671e7c31ccadcde7193060cd914112400248e441d38a6bde17c8f5cdfa2f6147` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/components/toaster.tsx` | `1b64bbbf05229dabc2c4859c484721919f95e2cf7db04a2bd0606d9dc1f10e10` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/auth.ts` | `28fcee6862329ab90a57e11b89cd968d6f1ea104416b72f3367150665f90ab9e` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/collection-tree.ts` | `3c4132fb1fb410738f1a06386dac7d615490d27d9108017b1a31875d5cda3db2` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/comparison-camera.ts` | `3eb14688704370c4730877c80f32fcd9ab08521525a80dedd7bd3b0f304e57f4` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/filter-labels.ts` | `8ff34e8e11c2f3e225594977a2210a6831ed9539f83170df120053263d71999a` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/gcode-worker-client.ts` | `96fe6e84d3d2665d9f9da20e94d5267f1ac939ce35f7f7df285f1aa9b2948a36` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/gcode-worker-protocol.ts` | `7879023671773e496c32a30006e7cccef33e4920e9cdffc7b370a091af037efd` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/gcode-worker.ts` | `fb3f4e6ffeb80095cda4e953f0b87a4b39dbd5633120407fa84f56ea4f48899d` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/library-startup-context.ts` | `293264d520f18f22b35f63d8ddb5cb61d80910bf966dfeef5d7796933fd0b71c` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/library-startup-provider.tsx` | `fba0e7796c9abf6faceadb6ba9bd4335a6ee2e7d13c39bc4f7220e34e88be610` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/link.tsx` | `7efe491eaeea51d09d39d48b97bd409d2ba5ad51fbb8410bde69788c6dcd48f5` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/mobile-filter-context.ts` | `dde16eed887d94b7e34290bb1c9614f3377f381237437baec427b22a1971338b` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/mobile-filter-provider.tsx` | `3340c53350247eb14ba3ce2967bf2371238150fb546f14e0fa89216236ddf013` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/model-dnd.ts` | `b97b300d6f79b4171b9e603a46a26cbcf8d4128785c82e04cc4f9a93f6ea5fc7` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/multipart-model-presentation.ts` | `571e545714dd3a7973ca2bf94d887e424117a6ec297e1deea46f7ec33bd89b73` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/startup-timing.ts` | `0d4b4da7b831ca9892ae58422a239dc903609044cdaeb382240c82db68176236` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/use-startup-thumbnails.ts` | `1378c6e02a2bae82c502d8d1350a56304d8ec67ecdb3c4d92ba6036984f45185` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/use-viewer-readiness.ts` | `b81e782f5ca6b0ec6a52b390b7be368b78eb4067896efc5f48f99aeeac388f51` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/types/auth.ts` | `d9b48241bdde1b841fdbcdac65df4da9c7c773b357adaf67f643378e47f7f20e` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/types/documents.ts` | `2b64097bc98835f4942963514dc30d09fadc6fea898f1e56066dcf8667cdd3a8` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/types/captions.ts` | `8292ab28d2349f57c7f7397e8d8e5d379c3ba9d433854e1255123722f007e52f` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/types/editing.ts` | `de80b6e017c00b45115b3e419f46eb0eeb3bf879ac95548eee4ba1e7cf1e0c83` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/types/inbox.ts` | `503cbd62678d7708b19933e2c19e76f8a9b55b22bc8bdf3a139fdab9306af759` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/types/library-browse.ts` | `730203bb74d7e754c7770867b174d90fde10dc9cf62507c9a33d7e758ee9c5db` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/types/maintenance.ts` | `7827e04bf15bdf4697aaefa08129b2921650f39064ee3859dc0a3e1f28843747` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/types/multipart-builds.ts` | `24c6f1f081a2a16fb1633685befae5f606f4936417258647eb33f0ec67892395` | Complete file; ownership/lifetime and public interface review. |
| `frontend/tests/e2e/i18n.spec.ts` | `3f8402963cdc8d5d89af7fbfd36bc5f566f67d20a56391fc6d04ee0f4c07a09d` | Complete file; ownership/lifetime and public interface review. |
| `frontend/tests/e2e-real/share.spec.ts` | `6bf2676773bbb092b33762bc739737a786ad7246e66855b8a631c90e7f262143` | Complete file; ownership/lifetime and public interface review. |
| `frontend/src/lib/__tests__/navigation.test.tsx` | `6d3fa4f15343c3b66bf31f7f900aa8cc64cdcb67b34779ca69daaa28c41f7164` | Complete file; ownership/lifetime and public interface review. |
| `frontend/tests/e2e/_setup.ts` | `b767052bbc58e71f45da6e51b118c35bcd78d4e7c22a16c3166026a5aa6922d2` | Complete file; ownership/lifetime and public interface review. |
| `frontend/playwright.real.config.ts` | `4962e3aabd4b407d61c4e5f8cdce22f5a294138e33faa77bcbb9f64958dca265` | Complete file; ownership/lifetime and public interface review. |
| `frontend/playwright.config.ts` | `a476d66ca26aa58c652fbf6176499096874149c46f43b70e85408071196070dc` | Complete file; ownership/lifetime and public interface review. |
| `frontend/vite.config.ts` | `aa3f363efd9faaec3847e0db7e5f10d8255590c844f6971abc435283fbcdb188` | Complete file; ownership/lifetime and public interface review. |

The Spoolman component test record above identifies the regression-bearing revision; the final pass inspected the setup and recovery assertions, not every pre-existing assertion in that file. Dependency-boundary tests were inspected by the named forbidden-edge assertion groups, not promoted to a complete-file read.

## Last asset compatibility removal

The complete asset-owner read found `getCachedAssetUrl` has no production callers. Mounted consumers already acquire/release leases. Remove this temporary compatibility reader and retain the same cache/retirement behaviors through the public lease interface; an identical Promise object is not an observable product contract.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| I8 | reuses one protected download across leases | Edge | concurrent consumers | same usable URL; one fetch | Frontend unit | ✅ `frontend/src/lib/__tests__/asset-cache.test.ts::collapses concurrent requests for the same path into one fetch` |
| I9 | retains session and eviction contracts after compatibility removal | Error | old request, invalidation, byte/entry pressure | old bytes retired; active lease preserved | Frontend unit | ✅ [M6 asset matrix](../frontend-m6-validation.md), requalified by 34 asset-cache/hook cases in 6.45 s |

The second complete instrumented app run reported 4,383 passing cases and five failures: one missing `describe` wrapper in the new resource observer (corrected), plus four UI waits in ModelGrid/FilterSidebar. Those four cases passed unchanged both in a regular focused run (18.06 s total) and in an instrumented focused run. They are recorded as suite-only timing failures, not claimed as fixed product defects. No global coverage result was emitted from either failed full run.

## Current owner reconciliation

The following complete reads check query ownership, cancellation, command acknowledgement, local drafts/secrets and session retirement. The settings owners keep feature-specific policies; they are not a universal command framework. Storage migration and accepted background work retain their existing backend authority.

| Path | Inspected SHA-256 | Scope / result |
|---|---|---|
| `frontend/src/lib/queries/settings-access.ts` | `9aad4af21a38acc9370aa384a4e6f9bdf340a0a4aed49a934ba8e3c9c7e04b57` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/lib/queries/settings-account.ts` | `9527186c879b2e99b2f6c2e145f00a6e9c70b33418f29252fc1b3e1966609445` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/lib/queries/settings-backup-catalog.ts` | `6f7364f8db6ed69b5431d31396f1bbb67c54c2b88207fb6ce9c4f590c0c3fc6f` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/lib/queries/settings-backup-policy.ts` | `0789ae43fe0519d600d676f900aac24f4f9f89e4a6809e599ae0c5575e675a1e` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/lib/queries/settings-backup-runs.ts` | `c4499946ad1a22a1e46adad7aff1bea9560257728056d68d44e3076af4fd31b6` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/lib/queries/settings-config.ts` | `5b722fb52ebe140f261621070aba10e75b6b6e678e2ed1ddd77c7640f6e45fe8` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/lib/queries/settings-system.ts` | `729c04f4aebc354df9fdf86656c347480dc5b7cf8ba66a317037400cb6d7b727` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/lib/queries/settings-storage-root.ts` | `52b2179337db799dc791f670f904d06183bcdb1cd944bb1fa8bb292e79aa951c` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/lib/queries/settings-providers.ts` | `7d81477f0e497c9bfb0daf947cc1fd52204ca6fdae71159dee98071de85ccaa2` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/lib/queries/settings-notifications.ts` | `e9bad2b910006d9542ae1122c7ae6fe47adbd213c64dda0bf13631146e54e3f6` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/lib/queries/settings-storage.ts` | `27485f7428b36369a1d54849965f602424abec7ad34e21ac7f6e89b3056a3d07` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/lib/queries/settings-library-sources.ts` | `08402997d9f5c39929a84fe8ffd29429b59cb6f59661432463deef985c2af802` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/lib/queries/settings-trash.ts` | `8d6266c1326235b0631ba78a4e7c0089bc0ad3ae5c9b0e899d34f037256f2029` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/lib/queries/settings-vault-migration.ts` | `2c57e0164a6e94598af71f30600a8e27dadd82608b23084d8b42e080e58890ff` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/lib/queries/settings-spoolman.ts` | `976128ca13c422903ef2f786fc5897f136451a7aed54b8b954a101c53adf3a9e` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/features/library/editing.ts` | `926fb56fa7433ebb3442dc120ec728e7e7150363ed8072360adad54b8eb1cf5e` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/features/library/metadata.ts` | `fbf63fc49ce463ede8255cac7216d8305c8b028959dc60d8de44911ea0a8353a` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/features/library/saved-views.ts` | `cccb51b5dc55bc5366a320792f66f4a4f02ec2d384ef2293da6730874d7ac5cf` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/features/library/taxonomy.ts` | `5575beef3e227be64fa1992afa91c6558655742272657bdd2fa7551b9c613d7d` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/features/library/thumbnails.ts` | `b935c3f84c6367ab0521b6bc1d704f85d3911acd0add355bbf1e9a0523aa97e1` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/features/library/url.ts` | `8d18705f63a2f84cde30282ec110d918c6181c475fae1ee03170e8813f5b46ea` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/src/lib/api/editing.ts` | `a315228a365527ed5b3b1ab94d123dcac9059a994efa4d36aa57d31130449e49` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/vite.config.ts` | `aa3f363efd9faaec3847e0db7e5f10d8255590c844f6971abc435283fbcdb188` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `frontend/package.json` | `f5219cb8ec71c86de5f3ec5508d8e0519f14f41e8708b738cede514f051fd811` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |
| `.github/workflows/deep-ci.yml` | `48c1d4472950cab1a67243f1db35e9e2cba5b126304fd477513dee221e735583` | Complete file. Ownership and integration contract reviewed against the final architecture and milestone matrices. |

## Platform and contract leaf reconciliation

The static deployment/test configuration and type/UI adapters below were read in full. `.mise.toml` and `components.json` retain historical Next.js scaffolding labels; these are disclosed tooling debt, not a production server runtime. The resource observer is test instrumentation, separate from comparable startup timing.

| Path | Inspected SHA-256 | Scope / result |
|---|---|---|
| `.mise.toml` | `d07c42d052f536fc0095bf60e5cad8922f7b2945f6ecabcc95f25b9619d0c2ab` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `backend/unified/.dockerignore` | `f24fa66559874ffd84ab7b9a6d7c7b04218374161f6d13889cfb13490a59e4f2` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/.dockerignore` | `d006aee81388da2367a311eb2ebed098fbea0855ba213ab5622a547a2d1b866a` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/.nvmrc` | `68ca3fba3b7e864770cb61aeb306d4bd4354b68ab4dd38450860c5d823e42a53` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/components.json` | `821edc55799233bfdc9bd04c9db5fb0fab5822def9325c98a35bb69a4f2415d2` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `browser-extension/styles.d.ts` | `a1e11ea9c568f92e5ff3b72ce5f9a47575e0b3c390a8b318e71f413af9f1b1a8` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `browser-extension/tsconfig.json` | `90f35ac966f4bf4354106f1469ce86db512ff72cdd103c590c238878f2fbbbb4` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `browser-extension/.gitignore` | `a5d030531186aa85db775b22b1476b17ed52f61ccb9b96e166e5b75f90ecb221` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `browser-extension/.oxfmtrc.json` | `c0dae20afb1d2ab41fcddb3dad2a1334bb114e6906aa62b3b955a270486c2ac8` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `browser-extension/entrypoints/popup/main.ts` | `2f390e4743c9e0ea85bd3c1480a9a3d4c1d992ebc163b1efb3ea29f793d25fbc` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/components/spoolman-connect-card.tsx` | `3c9d88d066f0a7b7ec8ef1f342e429dccbd2f1db0c698496af246e6fa90a3782` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/tests/performance/startup-resources.spec.ts` | `6689e26eb16f1207f494fcc8463f3a7a5c41f7ceb1ea2e590302cf486c7993b5` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/playwright.ai-search.config.ts` | `cc9da5364a2b4b35ee43f19f80181082f9d9c3750239da827b862a3ed967f512` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/playwright.critical-backup.config.ts` | `9df70c0a879d956e7f3df158cb975a55cd83e64781c4f2c055939bb8ff67449c` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/playwright.environment-admin.config.ts` | `fb973588107692fa1fa41bc0eb70743053dbe8c4f598cd8ecd7551458fbe9b86` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/playwright.migration.config.ts` | `85078ce3bf996087376bef24534d7cbb1273e4c6a36d6a20dea4bdc280d96117` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/playwright.onboarding.config.ts` | `6366697a36d71e2f07e30702bc23d19f321459fab8b7ff80b112aa0f4634600a` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/playwright.performance.config.ts` | `6e095f35f83638dcd831396de40dfd87d271abb4227ee6685c0e6058dc1fe3c9` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/playwright.startup.config.ts` | `62d5343a18c7abc6a76f733ac3d8fb21ac8420a6db1e83f74bd028fc81d09266` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/components/ui/badge.tsx` | `cbe95a1957a1237762b3bacaffee533c6d410357e46e7bf76bcfab9950420034` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/components/ui/button.tsx` | `cf3ce22a50898b40133ab3ad21b5d0cd01ef15225a28e08f3e910994663067e2` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/components/ui/card.tsx` | `e0fa924a222ddf3ac702d3e2fe9589323a2c9abb31697401824023fac922fe6b` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/components/ui/checkbox.tsx` | `a20902473cf3f558fad4f6dc3b27d6bfe4dc68c584ed5f9a88471ace5fdccca0` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/components/ui/drawer.tsx` | `78a9bee93bbd5f8f774b06e04e83b32a0bee353f215b6e7a039c35cb9bbbed70` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/components/ui/dropdown-menu.tsx` | `ed7f14b944a37b5ff4a4a16fe0d7426c32bba47fb7fb352b2ddd5db2a68f3f19` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/components/ui/empty-state.tsx` | `b93aaadddad57d916b40e0d4eb49cdf093db1def98e531d7758d9cedaaf40b7e` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/components/ui/input.tsx` | `bcecc49b4c1b7905e49f1a2261a98eb0b2b479fbcdc1d9abec0796c067dae882` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/components/ui/localized.tsx` | `77783ede5938648d0ab0f39779200325a3e5cd6e9490d27f251b7700bfb50883` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/components/ui/page-container.tsx` | `edf9c9f01c451b7f0f28c6c525ea024ce326e7b5df0b0f2e6232bfdf19614a19` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/components/ui/page-header.tsx` | `1ce2ae0b215b3050af01ab6e313d7a6d3c7415cbe0aafbfceb4362702cd6a6e3` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/components/ui/separator.tsx` | `0a9347e50163a34aae7759a05d0e604219c0eca1adb3f41335658317421cef0b` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/components/ui/skeleton.tsx` | `bcfea31b6ea70be082364e22481cd37ddf891ea4c971c508c0b2db9a40e9441f` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/components/ui/tabs.tsx` | `77a9d532fa1b3ae873277d30786aa52434315003e4f098b8bd4c40df8bf29b3b` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/types/notifications.ts` | `2c9b17472428126686c8ba54c9978cb00cf03e4b30048f60c20e32b788eb85a3` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/types/provider-connections.ts` | `5fc17194fa270e1879f3a973a76eb0890a8bf3f1b2deee323d9ea36fe25b0634` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/types/outliner.ts` | `335e5b0c3bab43814e41e39b6a5718f2fca5393488190bc8df1c07765868adad` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/types/provenance.ts` | `14a21cbc51ec303dff002fd7283b8862978a9e830b5e4de87641eb710e03386b` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/types/spoolman.ts` | `0cb466d8cae80d0266d1c7019c72d50afaaed115e3dd0c04aebaabfb7cdfb722` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |
| `frontend/src/types/index.ts` | `9f0fa5188dd3f6d534ce5dcd4f758e9bbbe10afd92a6cdd7cc3e585eec67f70b` | Complete file. Static/platform or public contract ownership reviewed; no new competing remote-state owner. |

## Deep browser integration corrections

Deep CI reproduced a stale onboarding URL assertion: canonical Library navigation
now includes `type=all&sort=date-desc`. Preserve the root-route and canonical-state
assertions instead of requiring the old query-free URL. The new resource observer
reached all 90 Models but used the retired sidebar sign-out control; it now uses
the actual account menu and has a bounded action timeout. Its dense-only corpus
is explicitly selected by the startup configuration.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| I10 | reaches its first Model entirely through browser controls | Happy | onboarding deferred/resumed | canonical Library URL and subsequent first Model workflow | Playwright real | ✅ `tests/e2e-real/onboarding/first-model.spec.ts` — normal + lost-response: 2 passed each |

The same retired URL assertion existed in partial-backup recovery, the Nextcloud
preset flow, Settings → Browse Models and AI Search → Clear Search. These now
assert the canonical root state explicitly; no navigation behavior was changed.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| I11 | retries the exact failed copy from partial backup success | Error | failed backup destination | exact destination retried after canonical login | Playwright real | ✅ `tests/e2e-real/critical/remote-backup.spec.ts::retries the exact failed copy from partial backup success` |
| I12 | edits a Nextcloud connection while preserving its linked target | Happy | existing connection with linked target | connection changes; linked target preserved | Playwright real | ✅ `tests/e2e-real/critical/remote-backup.spec.ts::edits a Nextcloud connection while preserving its linked target` |
| I13 | background work leads a new user to a model's preview status | Happy | Work page | Browse Models opens canonical Library URL | Playwright | ✅ `tests/e2e/settings.spec.ts::background work leads a new user to a model's preview status` |
| I14 | clears AI Search into the Library | Happy | populated Search | canonical root; empty search field | Playwright real | ✅ `tests/e2e-real/ai-search/search.spec.ts::submits semantic searches against a local index` — passed real suite |

## Coverage audit additions

Deep CI executed all app/domain/UI tests successfully. Its floor audit identified
missing edge coverage in optional preference persistence and polling lifecycle
contracts. The following behavior rows precede their additional assertions.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| C1 | rejects an unknown metric in any slot | Error | invalid stored first/second/third id | defaults returned | Frontend unit | ✅ `packages/domain/src/__tests__/card-metrics.test.ts` |
| C2 | reads default card metrics without a browser | Edge | window absent | copied defaults; no exception | Frontend unit | ✅ `packages/domain/src/__tests__/card-metrics.test.ts` |
| C3 | ignores card metric writes without a browser | Edge | window absent | existing stored choice unchanged | Frontend unit | ✅ `packages/domain/src/__tests__/card-metrics.test.ts` |
| C4 | reads default metadata without browser storage | Edge | localStorage absent | defaults returned | Frontend unit | ✅ `packages/domain/src/__tests__/metadata-preferences.test.ts` |
| C5 | ignores metadata writes without a browser | Edge | window absent | existing stored preferences unchanged | Frontend unit | ✅ `packages/domain/src/__tests__/metadata-preferences.test.ts` |
| C6 | starts only one delayed polling chain | Edge | repeated start before interval | one request at deadline | Frontend unit | ✅ `src/lib/__tests__/completion-chained-polling.test.ts` |
| C7 | forces polling until explicitly stopped | Edge | terminal result with force flag | another request; stop ends chain | Frontend unit | ✅ `src/lib/__tests__/completion-chained-polling.test.ts` |
| C8 | stops when a result callback disposes its consumer | Edge | onResult stops poller | no later result or request | Frontend unit | ✅ `src/lib/__tests__/completion-chained-polling.test.ts` |
| C9 | suppresses errors from retired polling work | Error | old rejected request after stop/restart | no stale error; new result delivered | Frontend unit | ✅ `src/lib/__tests__/completion-chained-polling.test.ts` |
| C10 | reports an opaque polling rejection safely | Error | rejected non-Error value | stable generic Error; recovery continues | Frontend unit | ✅ `src/lib/__tests__/completion-chained-polling.test.ts` |

| C11 | preserves modified Back-link gestures | Edge | modifier, middle click or new-tab target | native default preserved; current route unchanged | Frontend unit | ✅ `src/features/library/__tests__/navigation.test.tsx` |
| C12 | respects a cancelled Back-link gesture | Edge | caller prevents click | no navigation | Frontend unit | ✅ `src/features/library/__tests__/navigation.test.tsx` |
| C13 | follows an unknown-origin Back fallback | Happy | direct detail link | safe return view visible | Frontend unit | ✅ `src/features/library/__tests__/navigation.test.tsx` |

Deep-CI browser failures also exposed old test clients parsing Model IDs from
the entire href (now containing a return query), exact query-string comparisons,
an incomplete mocked adoption receipt, an error collector installed after navigation
and a Multipart test confusing 255-character Model names with 128-character part
names. Corrections preserve the required assertions and update their inputs to the
current contracts; the affected browser selections remain to be qualified.

### Permission-command failure qualification

M9 requires an uncertain permission write to recover the authoritative resource without retrying the write. The same contract applies to collection and printer permissions.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| C14 | refreshes grants after an uncertain grant | Error | Server applies grant but PUT response is lost; collection/printer | Fresh grant displayed, error retained, one write | Frontend unit | ✅ `src/lib/queries/__tests__/settings-access.test.tsx` — both permission resources |
| C15 | refreshes grants after an uncertain revocation | Error | Server removes grant but DELETE response is lost; collection/printer | Fresh empty permission list, error retained, one write | Frontend unit | ✅ `src/lib/queries/__tests__/settings-access.test.tsx` — both permission resources |
| C16 | keeps other grants in username order after acknowledgement | Edge | Multiple existing grants; collection/printer | Updated grant rendered once in alphabetical order with other grants retained | Frontend unit | ✅ `src/lib/queries/__tests__/settings-access.test.tsx` — both permission resources |

### Local qualification limits recorded during M11

The selected mock-browser correction run passed **8 tests in 1.1min**, covering
cached preview, mobile Pending Imports, live/AI search at two widths, S3 adoption,
Work-to-Library navigation, long Multipart names and browser Back. Lint and app/UI/
domain typechecks passed after the permission recovery cases were added; those
16 permission cases passed under focused coverage. Navigation/poller selection:
26 passed. Domain coverage: 97.20% statements, 93.91% branches.

The local full-backend run was deliberately interrupted to avoid duplicating the
ongoing Deep CI backend qualification while the local host exhausted available
memory. It reported **9,370 passed, 1 failed**, not a passing full gate. The failure
was `test_records_selected_load_cells[retry-eight-only]`: seven of eight selected
artifacts became usable; one exceeded the 120s Job deadline. Natural drain finished.
That diagnostic is retained, with no timeout change or production workaround.

A concurrently started local frontend coverage run reported **4,376 passed,
32 failed (13 files)** in 481.50s; the failures were timeouts or rendered waits.
No coverage report was emitted. The host had 7.7GiB RAM and nearly its entire 2GiB
swap in use. The prior CI run's complete unit suite was green, but this does not
make the local failed run green: a bounded follow-up and final CI remain required.
Two local AI-search invocations never reached a test (missing uv on PATH, then
backend-startup timeout under that load); neither is a behavioral result.

### Search transport regression discovered by real browser qualification

`readSearchFilters` includes persisted view metadata, while `GET /search` accepts
strict `ModelFilters`. Sending `library_view` causes HTTP 422
`model_filters_invalid` for any filtered Search. Search owns its result-kind
selection independently; Library mode must not become a search predicate. Project
saved-view-only metadata out at the API boundary; retain every actual predicate.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| C17 | sends Model predicates without saved-view metadata | Error | Filtered search from all/multipart saved view | Wire JSON contains print/tag predicates; query and sort remain top-level; no Library-only mode | Frontend unit | ✅ `src/lib/api/__tests__/search.test.ts::sends Model predicates without %s saved-view metadata` — red 2, green 2 |
| C18 | persists editable filters without repeated parsing | Happy | Real filtered Search after NL interpretation | Matching Model remains visible; saved view reopens without another chat call | Playwright real | ✅ `tests/e2e-real/ai-search/nl-filters.spec.ts::persists editable filters without repeated parsing` — 1 passed, 29.4s |

### PostgreSQL contract fixture reconciliation

Deep CI resources reported 679 passed / 7 failed. Four edit tests still sent
pre-epoch ETags, so they never exercised concurrent claims or revoked actors.
The capacity upgrade test constructed a current ORM Model against an old schema.
The vector degradation role lacked the INSERT required by the newly installed
browse-revision trigger. The generation schema test downgraded a populated head
through a historical, explicitly data-dropping cursor migration; its requirement
is forward schema compatibility, which should start from the released fixture.
No merged migration is edited and extension creation remains denied.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| C19 | permits only one atomic editor | Edge | Model/Multipart/Document concurrent claims with current epoch | One persisted winner, one conflict | Integration PostgreSQL | ✅ `backend/tests/integration/postgres/test_library_browse.py::test_permits_only_one_atomic_editor` — seven-case selection passed |
| C20 | rejects revoked actor | Error | Actor deactivated after authorized read; valid editing base | Permission denial, unchanged edit version | Integration PostgreSQL | ✅ `backend/tests/integration/postgres/test_library_browse.py::test_rejects_revoked_actor` — seven-case selection passed |
| C21 | upgrade preserves Models | Edge | Model stored in pre-capacity released schema | Name survives upgrade to head | Integration PostgreSQL | ✅ `backend/tests/integration/postgres/test_capacity.py::test_upgrade_preserves_models` — seven-case selection passed |
| C22 | degrades when pg extension cannot be created | Error | DML-capable application role without CREATE EXTENSION | Unavailable native index, NumPy results usable | Integration PostgreSQL | ✅ `backend/tests/integration/postgres/test_vector_index.py::test_degrades_when_pg_extension_cannot_be_created` — seven-case selection passed |
| C23 | keeps migrated PostgreSQL schema in sync | Edge | Released schema advanced through generation migration | Head matches ORM; no phantom generation | Integration PostgreSQL | ✅ `backend/tests/integration/postgres/test_search_generations.py::test_keeps_the_migrated_postgres_schema_in_sync` — seven-case selection passed |

Search client current-file reconciliation:

| Path | Inspected SHA-256 | Scope / result |
|---|---|---|
| `frontend/src/lib/api/search.ts` | `cea8520d01de82bd6834d559bb348dae89b6ed0be12444b2d907d7c46963d120` | Complete file. URL/saved-view metadata is projected out of the strict Search request contract; actual predicates are retained. |
| `frontend/src/lib/search-filters.ts` | `905546a7fe3a3c2f1c77ea393aa2e8465b67a9a4fd70a6cf2a4fdbe0334e36b3` | Complete file. URL/saved-view metadata is projected out of the strict Search request contract; actual predicates are retained. |
| `frontend/src/features/library/filters.ts` | `ac4d9f27c208dd7990cdc3ad7bd9723ae56dd5e9fe1dfbcb5577abff5d6a20c1` | Complete file. URL/saved-view metadata is projected out of the strict Search request contract; actual predicates are retained. |

The PostgreSQL correction selection passed **7 tests in 136.47s** against real
containers. The 32 frontend cases that failed under local saturation passed
unchanged in **37.20s with `--maxWorkers=2`** (13 files, 856 unrelated cases not
selected). This is a bounded recheck, not a global coverage result. No assertion
or timeout was loosened. The Search API regression and permission/hygiene selection
passed **46 tests in 4.62s**; real NL-filter workflow passed in **29.4s**.

### Ordered-run database isolation and remaining browser qualification

Required CI on `6eabc187` exposed two `files` browse-trigger failures. Both pass
alone; running `TestMeasureIngestion` first with `--randomly-dont-reorganize`
reproduced **2 failed / 3 passed in 3.84s**. The benchmark error test dropped
the shared `files` table and recreated only its columns, losing its triggers.
The correction must isolate its intentionally broken schema, rather than repair
production DDL or make unrelated tests reinstall the missing triggers.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| C24 | retains source probe failure | Error | Separate database without the source table | Failed observation retains probe timing and missing-table reason | Integration | ✅ `backend/tests/integration/scripts/test_benchmark_ingestion.py::TestMeasureIngestion::test_retains_source_probe_failure` — corrected ordered selection: 73 passed in 55.63s |
| C25 | rejects continuation after each catalog dependency writer | Edge | Benchmark failure test precedes a committed file change | Old browse cursor is rejected, file triggers remain installed | Integration | ✅ `backend/tests/integration/db/test_library_contracts_v1.py::TestInstall` — existing assertions, corrected ordered selection: 73 passed in 55.63s |

Real captions and Similarity regression selection passed **3 tests in 2.4min**.
The isolated ingestion retry-eight case passed **1 test in 171.35s**; this does
not replace the interrupted full-suite result recorded above.

The real-browser document failure is a production lifetime defect: SetupGate
unmounts the previously admitted application on every pathname probe, destroying
the new document's selected edit mode. Preserve the admitted subtree's state
while hiding it during the pending probe, using React's installed Activity API;
initial admission still mounts nothing, rejected admission removes the subtree,
and AuthProvider's session key still retires the entire private tree.
[React Activity contract](https://react.dev/reference/react/Activity): hidden
content retains state while its effects are cleaned up.

Storage's real legacy configuration returns an empty `storage_provider` with an
explicit `storage_backend`. The current card's nullish fallback treats the empty
provider as a catalog ID, hiding the actual configured paths. Interpret that
existing legacy wire contract explicitly, retaining the backend selection.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| C26 | preserves admitted UI state across a setup probe | Edge | Typed draft, path changes, pending then configured status | Hidden while pending; same draft visible after admission | Frontend unit | ✅ `frontend/src/components/__tests__/setup-gate.test.tsx` — 47-case gate/auth/router selection passed |
| C27 | removes admitted content when setup rejects the next entry | Error | Previously admitted draft; next status unconfigured | Private draft removed; setup navigation occurs | Frontend unit | ✅ `frontend/src/components/__tests__/setup-gate.test.tsx` — rejection retires hidden state; selection passed |
| C28 | shows legacy backend storage paths | Edge | Empty provider ID, explicit local/S3 backend | Correct provider and configured location visible | Frontend unit | ✅ `frontend/src/components/__tests__/storage-config-card.test.tsx` — local/S3 cases passed |
| C29 | preserves new-document editing through route admission | Happy | Real create receipt replaces new-document route | Editor survives; Preview, edit and save lifecycle succeeds | Playwright | ✅ `frontend/tests/e2e-real/documents.spec.ts` — corrected real workflow passed (14.0s) |
| C30 | targets the tag editor with existing suggestions | Edge | Multipart tag dialog contains input and suggestion list | Local tag can be entered before conflict review | Playwright | ✅ `frontend/tests/e2e-real/multipart-models.spec.ts` — role-specific input locator; real workflow passed (13.8s) |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| C31 | discards ownerless legacy browser upload queues after reload | Error | Legacy localStorage rows have no verified session owner | No task description is exposed; stale rows remain absent after reload | Playwright | ✅ `frontend/tests/e2e-real/uploads.spec.ts::discards ownerless legacy browser upload queues after a reload` — passed (3.2s) |
| C32 | renders an asymmetric preview with a downloadable screenshot | Happy | Real mesh; collection tiles put its card below viewport | Scrolling the card into view admits and decodes thumbnail; screenshot downloads | Playwright | ✅ `frontend/tests/e2e-real/uploads.spec.ts` — visible-card precondition corrected; six-case real selection passed |
| C33 | renders an uploaded STL thumbnail | Happy | Real mesh-only upload in populated library | Visible card decodes authenticated thumbnail; source Artifact is retained | Playwright | ✅ `frontend/tests/e2e-real/uploads.spec.ts` — visible-card precondition corrected; six-case real selection passed |

The old CI thumbnail traces contain ready thumbnail URLs in browse and thumbnail
DTOs, but no image GET: collection tiles placed the cards below the viewport.
M6 deliberately admits visible thumbnails first. The browser assertion must scroll
the card into view before waiting for image decoding. The ownerless legacy queue
expectation predates M7 isolation; exposing its descriptions would regress that
contract. Neither correction changes production thumbnail admission or ownership.

Full frontend coverage on `6eabc187` passed: **4,410 app + 85 domain + 199 UI
tests**, every floor held. App statements/branches **86.59%/81.14%**, domain
**97.20%/93.91%**, UI **98.78%/97.60%**. The gate reports maintenance notices
for improved floors; none was lowered. Later gate/storage corrections have
focused evidence and require the final-sha CI checks below.

Gate/storage/document unit selection: **105 passed**, one new test initially
found rejected hidden state surviving redirect. The gate now explicitly retires
that subtree before redirect. Corrected gate/auth/router selection:
**47 passed in 6.59s**. Real document/Multipart/storage selection:
**3 passed in 1.4min**. Lint and app/package type checks passed.

The older Python compatibility job completed **20,187 passed / 2 failed in
3,571.27s**, then its job deadline marked it cancelled during cleanup. Its only
assertion failures were the reproduced, corrected shared-table isolation defect.
This is failure evidence, not a passing full gate.

Final selected real-browser run: **6 passed in 1.3min**, including all three
Document conditional-write scenarios, legacy task isolation and both mesh preview
flows. Formatting passed across 793 files.

### First-page read contention

The remaining Share/ZIP flakes showed an empty initial library with
`browse_refresh_required`, not a missing uploaded Model. A background writer
changed the catalog during the backend's first-page authority window. A single
fresh first-page retry is safe before a cursor has been accepted; continuation
conflicts must still preserve the displayed snapshot and require explicit refresh.
Sustained churn still surfaces recovery after that bounded retry. This does not
relax the backend revision fence or merge inconsistent pages.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| C34 | recovers first-page read contention once | Edge | First page returns browse_refresh_required, fresh request succeeds | A coherent first page appears without user refresh | Frontend unit | ✅ `frontend/src/features/library/__tests__/browse.test.tsx` — red/green verified |
| C35 | exposes sustained first-page contention | Error | Both first-page attempts conflict | Explicit refresh required after exactly two requests | Frontend unit | ✅ `frontend/src/features/library/__tests__/browse.test.tsx` — exactly two attempts |
| C36 | does not retry authorization rejection | Error | First page returns 403 | No retry or private page publication | Frontend unit | ✅ `frontend/src/features/library/__tests__/browse.test.tsx` — one denied request |
| C37 | retains displayed pages when continuation requires refresh | Edge | Accepted first page, next cursor conflicts | Existing page unchanged; exactly one continuation request | Frontend unit | ✅ `frontend/src/features/library/__tests__/browse.test.tsx` |
| C38 | auto-mark-known-good toggle persists across reload | Happy | Successful PUT followed by Query receipt publication | Wait for rendered checked state, then verify reload and restore original | Playwright | ✅ `frontend/tests/e2e-real/settings.spec.ts` — replace instantaneous post-response read with observable wait |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| C39 | does not revive a disposed first-page conflict | Error | Unmount while first response is pending; late conflict arrives | Original request aborted, no recovery GET or page publication | Frontend unit | ✅ `frontend/src/features/library/__tests__/browse.test.tsx` — nine-case file passed |

The scalar-toggle recheck exposed an additional test race: the test read the
disabled placeholder's `false` before configuration arrived, then Playwright
waited for the enabled `true` control before clicking. Its expected opposite
therefore used the wrong base. Await the enabled control before reading its
value, then await receipt publication before reload. Production already blocks
the unloaded control. The other four selected Share/filter/ZIP flows passed.

Browse/authority/mutation selection passed **45 tests in 5.84s**; the final
browse file including cancellation passed **9 in 3.34s**. The corrected scalar
toggle passed against the real backend in **6.7s** (46.1s including startup).

### Authenticated first-run guide ownership

The final M9 caller review found local copies of configuration/catalog reads in
`SetupStorageChoice` and Model/location reads in `GettingStartedPage`. A locale
change reruns the choice bootstrap and overwrites a typed path; leaving the guide
does not abort preparation and its late receipt can start new reads. Move the
reads to named Query projections, retain only form drafts and workflow UI locally,
and scope preparation to the mounted entry and current session. Credentials must
never enter MutationCache. This is completion of M9 before M11 qualification.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| C40 | retains a storage draft when locale changes | Edge | Typed local root; switch locale | Root remains typed; no duplicate bootstrap GET | Frontend unit | ✅ Guide/choice red/green selection |
| C41 | retires a pending storage choice on unmount | Error | Delayed preparation receipt after leaving | Request aborted; no completion callback | Frontend unit | ✅ Guide/choice red/green selection |
| C42 | shares configuration with the setup storage picker | Happy | Configuration Query already loaded | Picker uses canonical cached roots without a duplicate read | Frontend unit | ✅ Guide/choice red/green selection |
| C43 | retires preparation before starting guide reads | Error | Guide unmounts while preparation pending | Aborted command; no Model/location request | Frontend unit | ✅ Guide/choice red/green selection |
| C44 | retires guide catalog reads on unmount | Error | Delayed Model/location responses | Both network signals aborted; no retained result | Frontend unit | ✅ Guide/choice red/green selection |
| C45 | retries catalog loading | Error | Catalog fails after successful preparation | Retry reveals verified Model without replaying preparation | Frontend unit | ✅ Existing `getting-started.test.tsx` |

The five new guide/choice regressions first failed against the old ownership.
After the cutover, both complete files passed **34 tests in 9.29s**. Configuration
and provider metadata reuse their existing canonical Query keys; first-run Model
preview (limit 5) and optional directory suggestions have cancellable named keys.
Preparation is entry/session scoped, invalidates sanitized configuration only after
a current receipt, and keeps credentials outside MutationCache. Lint and app/package
type checks passed. No automatic write replay was added.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| C46 | retires a storage choice when the session changes | Error | Pending choice; session clears before receipt | Request aborted, no continuation, no cached command credentials | Frontend unit | ✅ `setup-storage-choice.test.tsx` — final selection passed |
| C47 | retains a storage draft during config refresh | Edge | Typed path; canonical config refetch publishes new defaults | Typed path remains unchanged | Frontend unit | ✅ `setup-storage-choice.test.tsx` — final selection passed |

Final choice/boundary selection passed **88 tests in 4.25s**, including session
retirement, canonical config refresh and the declared guide public interface.
The preceding four-file selection passed 117 cases and exposed one incorrectly
specified new boundary fixture; that fixture now declares its transport source.
The real environment-provisioned owner flow passed **1 test in 1.0min** (14.9s
for storage choice through first Model). Required PR CI on `dcfc34e5` completed
successfully. The preceding Deep browser run (`6eabc187`) reported **122 passed,
6 failed, 1 flaky**; its six failures map to C26–33 and the flaky toggle to C38.
Those old-run failures are retained as evidence, not relabeled as a green run.

### Final guide and browse revision inspection

| Path | Inspected SHA-256 | Scope / result |
|---|---|---|
| `frontend/src/components/setup-storage-choice.tsx` | `7cb7c5110aa632e893676b7d467e14e58f62b22e7e2fff3cc70596acf6a9412b` | Complete file; guide lifetime, shared read ownership, admission preservation or bounded read-conflict recovery as described above. |
| `frontend/src/pages/getting-started.tsx` | `22edd2b714f4ff84c42b07605a5f2f00d0ffb8b270d9b09207b4a0a14f04c871` | Complete file; guide lifetime, shared read ownership, admission preservation or bounded read-conflict recovery as described above. |
| `frontend/src/features/setup/guide.ts` | `aa1274c655bccf33bab731aba4992f2833115ed967e55a7e6d53a56c2cf11231` | Complete file; guide lifetime, shared read ownership, admission preservation or bounded read-conflict recovery as described above. |
| `frontend/src/features/library/browse.ts` | `ca9d0b839b2cbf20c57a68115d6b5fc5c5e54cb1c00b2bcc2e01877b70f0fb71` | Complete file; guide lifetime, shared read ownership, admission preservation or bounded read-conflict recovery as described above. |
| `frontend/src/components/setup-gate.tsx` | `4717cf44276cf109e7177fa3f6b51e9e7b61dc002f2783409d09224869fce264` | Complete file; guide lifetime, shared read ownership, admission preservation or bounded read-conflict recovery as described above. |
| `frontend/src/lib/session-transport.ts` | `e266d7300224997fb0c8c983f6738b04cc95f138fa1613667a78f4d92abf91ba` | Complete file; guide lifetime, shared read ownership, admission preservation or bounded read-conflict recovery as described above. |
| `frontend/src/components/__tests__/setup-storage-choice.test.tsx` | `7285de57b3c73cbb173e00f44fd0139f469b9783c54773db8d937c240c0ed14e` | Complete file; guide lifetime, shared read ownership, admission preservation or bounded read-conflict recovery as described above. |

### Artifact cache administration completion (M9 dependency)

The previously recorded ArtifactCacheCard debt remained in the live Settings
route: copied remote usage/policy, a competing one-second interval, unfenced
command receipts and no conditional policy/reset contract. This requires a bounded
owner cutover, not removal of safe materialization behavior. The cache policy is
stored on SystemConfig, so it uses the existing vault editing epoch/version and
atomic claim. No new table or migration is needed. Clear affects disposable bytes;
save/reset affect policy. Background usage updates must not change a dirty policy.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| C48 | rejects obsolete cache policy saves | Error | Two edits share original base | Second save rejected; winner policy retained | Backend integration | ❌ |
| C49 | rejects obsolete cache policy resets | Error | Concurrent save before reset | Reset rejected; winner remains persisted | Backend integration | ❌ |
| C50 | advances cache edit identity for legacy writes | Edge | Unversioned compatibility write | Prior conditional base no longer accepted | Backend integration | ❌ |
| C51 | requires a base for opted-in cache policy writes | Error | Conditional contract header without If-Match | 428, no policy write | Backend integration | ❌ |
| C52 | shares cancellable cache maintenance reads | Edge | Maintenance active; delayed read | Single in-flight read; disposal aborts it | Frontend unit | ❌ |
| C53 | preserves cache policy draft during usage refresh | Edge | User edits size; maintenance read completes | Typed size stays; usage updates | Frontend unit | ❌ |
| C54 | preserves cache policy draft when clearing bytes | Edge | Dirty policy then clear | Usage receipt published; dirty policy unchanged | Frontend unit | ❌ |
| C55 | requires explicit review after a cache save conflict | Error | Save receives 412 or uncertain response | Draft retained; ordinary resubmit blocked; authorized adoption available | Frontend unit | ❌ |
| C56 | retires cache command feedback with its session | Error | Session ends before receipt | No stale toast, draft reset or Query publication | Frontend unit | ❌ |
| C57 | clears obsolete cache reads before acknowledging writes | Edge | Old usage/policy read races confirmed save | Aborted stale result cannot replace receipt | Frontend unit | ❌ |
| C58 | recovers a denied cache read without private controls | Error | Previously loaded policy then 403 | Private controls hidden; explicit retry | Frontend unit | ❌ |
| C59 | reviews a cache policy conflict in the browser | Edge | Two editors; one wins | Other preserves draft and explicitly adopts current policy | Playwright real | ❌ |

### Dependency recheck before administration closure

The complete remaining effect-owned reader scan also identifies live
`PrinterMaterials` (M7) and `MaintenancePanel` (M9) callers. Materials already has
a backend `expected_updated_at` contract, but its catch reload discards a conflicted
draft. Maintenance keeps audit/backup snapshots and a second interval. Reopen these
bounded acceptance gaps in dependency order: Materials before administration cache
and maintenance. Earlier milestone evidence is retained, not presented as proof
that these callers were migrated.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| C60 | preserves manual material drafts after conflict | Error | Dirty feed; write returns 409 | Draft retained, no automatic reload/rebase; review offered | Frontend unit | ✅ `printer-materials.test.tsx` — 19-case final file passed |
| C61 | retires material reads when changing printer | Edge | Old printer read pending; switch printer | Old read aborted; only new printer fields rendered | Frontend unit | ✅ `printer-materials.test.tsx` — 19-case final file passed |
| C62 | separates material read failure from loading | Error | Initial material read fails | Error/retry, no endless skeleton | Frontend unit | ✅ `printer-materials.test.tsx` — 19-case final file passed |
| C63 | adopts reviewed material state explicitly | Happy | Conflict then authorized latest read | Adoption replaces local draft without another write | Frontend unit | ✅ `printer-materials.test.tsx` — 19-case final file passed |
| C64 | preserves manual draft during provider refresh | Edge | Typed manual material then new remote state | Draft/base stay original; provider observation updates | Frontend unit | ✅ `printer-materials.test.tsx` — 19-case final file passed |
| C65 | retires material save feedback on unmount | Error | Write response arrives after leaving | Request aborted; no success feedback/cache publication | Frontend unit | ✅ `printer-materials.test.tsx` — 19-case final file passed |

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| C66 | submits semantic searches against a local index | Edge | Keyword navigation normalizes other Library parameters | Decoded q remains intact independently of parameter order | Playwright real | ❌ Correct stale `/?q=` prefix expectation; pending selected execution |

Deep CI on `90ecfbc0` passed environment-owner setup; its later AI Search test
failed because it required q to be the first URL parameter. Actual canonical URL
was `/?type=all&sort=date-desc&q=bike+lamp+attachment`. Assert path and decoded q,
not serialization order. The old `6eabc187` ordinary backend lane finished
**20,187 passed / 2 failed in 2,586.71s**; both failures are C24–25's corrected
shared-table isolation defect. The superseded `66ba9036` coverage invocation was
cancelled after its ordinary portion passed **20,189 tests in 3,756.77s**; it was
still executing resources. This is not a complete coverage result.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| C67 | reviews a manual material conflict in the browser | Edge | Two printer editors save different nozzles | Loser draft retained until explicit adoption; backend winner unchanged | Playwright real | ✅ `printer-materials.spec.ts` — 1 passed (7.5s body, 51.3s total) |

Manual Materials M7 caller is qualified: the original six added cases produced
five failures (the original local draft already survived a passive query-cache
operation, which did not yet refetch its private reader). After migration, the
strengthened provider-refresh assertion proves a new AMS observation appears
without replacing the manual draft/base. The final 19-case file passes; broader
material/API/dependency selection passed **141 tests in 4.44s**. Lint and all
frontend type checks passed. The real two-editor nozzle conflict passed and
requires explicit adoption, preserving the existing backend timestamp contract.
No stronger backend material-state concurrency guarantee is claimed here.
