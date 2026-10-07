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
