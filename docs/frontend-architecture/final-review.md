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
| `frontend/playwright.startup.config.ts` | `7496247f37c8833363b74577c1d886c321a88acfbeec6c4466d392b0fccd29a3` | Complete file. Production measurement observer and owned fixture/proxy lifecycle retained. |
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
| I8 | reuses one protected download across leases | Edge | concurrent consumers | same usable URL; one fetch | Frontend unit | ❌ lease-suite qualification pending |
| I9 | retains session and eviction contracts after compatibility removal | Error | old request, invalidation, byte/entry pressure | old bytes retired; active lease preserved | Frontend unit | ❌ existing asset behavior matrix requalification pending |

The second complete instrumented app run reported 4,383 passing cases and five failures: one missing `describe` wrapper in the new resource observer (corrected), plus four UI waits in ModelGrid/FilterSidebar. Those four cases passed unchanged both in a regular focused run (18.06 s total) and in an instrumented focused run. They are recorded as suite-only timing failures, not claimed as fixed product defects. No global coverage result was emitted from either failed full run.
