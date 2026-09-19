# LAN upload regression coverage

Requirement: uploads through ordinary local-network HTTP preserve whole-file and
part SHA-256 verification without reporting a server connection error.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | persists a 112 KiB STL without SubtleCrypto | Happy | Browser without native digest; real API | Model visible after reload | Playwright | ✅ `tests/e2e-real/lan-upload.spec.ts::persists a 112 KiB STL without SubtleCrypto` |
| 2 | preserves SHA-256 without native browser digest support | Edge | Empty, ASCII, Unicode, multi-block content | Independent SHA-256 digest matches | Frontend unit | ✅ `src/lib/__tests__/artifact-upload.test.ts::preserves SHA-256 without native browser digest support` |
| 3 | retains native digest support in secure contexts | Happy | Native WebCrypto; abc | Standard SHA-256 digest | Frontend unit | ✅ `src/lib/__tests__/artifact-upload.test.ts::retains native digest support in secure contexts` |
| 4 | bounds fallback reads while preserving binary bytes | Edge | Binary content over 2 MiB | Correct digest; reads at most 1 MiB | Frontend unit | ✅ `src/lib/__tests__/artifact-upload.test.ts::bounds fallback reads while preserving binary bytes` |
| 5 | preserves read failures | Error | Unreadable Blob | Original error propagated | Frontend unit | ✅ `src/lib/__tests__/artifact-upload.test.ts::preserves read failures` |
| 6 | verifies upload checksums without native browser crypto | Happy | Default upload digest; two parts | Correct whole-file and part digests; completed upload | Frontend unit | ✅ `src/lib/__tests__/artifact-upload.test.ts::verifies upload checksums without native browser crypto` |

The regression was reproduced with the same source on localhost (successful digest)
and an ordinary LAN HTTP origin (TypeError mapped to network_unreachable).

Verification: seven regression cases failed before the fix. All 13 upload unit
tests pass after the fix, together with the real-backend browser regression.
The same browser digest loop now succeeds on both localhost and ordinary LAN
HTTP without changing browser security settings. Lint, formatting and type checks pass.
