# Extension capture validation

Base `710e4eb7`; qualified in the shared `refactor/library-navigation-contracts` worktree. This document owns the extension behavior matrix; backend browse, conditional edits and upload cleanup are qualified separately in [library-contracts-validation.md](library-contracts-validation.md).

The capture operation freezes normalized vault, configuration and authorization together before awaiting permissions, provider reads or downloads. Connection changes/disconnect retire its controller. Every deferred publication, including metadata fallbacks and the old operation's finally block, must still belong to the current operation. Cleanup is limited to the original acknowledged capture slot with its original credential and a separate bounded lifetime.

## Behaviour matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 92 | extension_capture_connection_binding | Security/regression | held provider download, vault A→B | old download aborted; no A credential request to B; current connection feedback preserved | Extension DOM integration | ✅ `browser-extension/tests/popup.test.ts` (held Thingiverse transfer RED→green) |
| 93 | extension_capture_disconnect_retirement | Security/edge | held request then disconnect/new capture | old response/error/finally cannot publish or clear newer workflow | Extension DOM integration | ✅ `browser-extension/tests/popup.test.ts` |
| 94 | extension_owned_cleanup_lifetime | Security/error | aborted slot upload with known item | bounded dismissal uses original vault/credential only | Extension transport | ✅ `browser-extension/tests/capture-transport.test.ts` |
| 95 | extension_invalid_login_body | Error boundary | HTTP200 malformed JSON/null | intended missing-access-token error instead of TypeError | Extension integration | ✅ `browser-extension/tests/core.test.ts` (null/malformed JSON RED→green) |
| 96 | loaded_extension_capture_retirement | Headline | installed extension switches vault during held capture, then captures with new connection | abort original HTTP, preserve current feedback, next write uses new vault/credential | Loaded-extension E2E | ✅ `browser-extension/tests/e2e/loaded-extension.e2e.ts` (native browser APIs + held loopback HTTP) |
| 97 | extension_installer_acknowledged_identity | Contract | supported driver path install | exact directory and returned valid assigned id, no guessed id | Extension helper | ✅ `browser-extension/tests/chrome-extension.test.ts` |
| 98 | extension_installer_failure | Error | driver error/invalid assigned id | error propagated or invalid id rejected | Extension helper | ✅ `browser-extension/tests/chrome-extension.test.ts` |
| 99 | extension_metadata_retirement | Security/regression | held visible/Thingiverse/Printables metadata during vault switch | no fallback selector/status publication; provider HTTP aborted | Extension DOM integration | ✅ `browser-extension/tests/popup.test.ts` (Thingiverse fallback and Printables cancellation RED→green) |

## Extension qualification and scope

- Exact deferred Thingiverse download/vault-switch DOM regression first failed by sending the synthetic vault A credential to synthetic vault B's upload-slot endpoint. The frozen operation now captures normalized vault, scalar config and credential together before the first await. Connection updates/disconnect retire and abort old work; stale completion/error/finally cannot publish or clear a newer capture. Admission starts before permission awaits.
- Cleanup after an acknowledged upload slot is separate, bounded at 5 seconds, and targets only the original slot/vault/credential. A lost creation acknowledgement has no proven item id and does not fabricate one; users retain Pending Imports cleanup controls. Provider timeout diagnostics still use their existing error contract. Malformed/null successful login JSON now raises the intended missing-access-token error.
- Final extension gates: format check, lint, typecheck, Chrome/Firefox/Edge builds, 179 tests across 13 files passed. Loaded Chrome 148.0.7778.96 + matching ChromeDriver gate: all 3 cases passed in 8 seconds after the metadata retirement amendment, including real installed popup/native storage and tabs APIs, held loopback HTTP cancellation on vault change, preserved feedback, and subsequent capture with the new vault/credential. The popup is loaded as an extension tab, consistent with the existing smoke; no browser API is replaced. This does not claim a live provider exploit reproduction or Firefox loaded-browser qualification.
- Browser test prerequisite independently reproduced RED before popup assertions: the prior browser WebSocket installer received `Extensions.loadUnpacked: Method not available`. [Chromium's protocol implementation](https://chromium.googlesource.com/chromium/src.git/+/225b2eaa7f23c33b7c4e30c1bfc58f1bd99cbe1c) restricts unsafe extension commands to pipe clients with the explicit debugging flag. The test now uses the already-installed [WebDriver BiDi extension install contract](https://www.w3.org/TR/2026/WD-webdriver-bidi-20260216/#command-webExtension-install) through ChromeDriver, with required returned id. Four helper cases were RED→green and cover exact directory, assigned id, error propagation, and invalid id rejection. Firefox/dependencies remain unchanged. A native toolbar `action.openPopup` fixture stalled in headless Chrome and was retired after that first failure; the existing installed popup-tab test pattern was used instead.

- Coordinator review identified a late metadata fallback after retirement. Deferred Thingiverse file metadata reproduced reopening the manual selector; deferred Printables metadata separately reproduced an un-aborted HTTP signal. The full visible metadata read is now guarded by the operation, followed by an explicit current-context check before routing/rendering, and the Printables stage receives the parent signal. All three deferred metadata cases pass. Final affected popup/transport 59 cases passed; repeated full extension/builds 179 passed, lint/typecheck/format clean, and loaded 3-case Chrome flow passed against that final build.

## Known gaps and scope limits

- Connection establishment is qualified separately in [extension connection validation](extension-connection-validation.md), including Cancel, failed updates, credential publication, and permission cleanup.
- A lost slot-creation acknowledgement provides no owned item id. Cleanup does not guess one; existing Pending Imports dismissal/expiry remains the recovery path.
- Loaded-browser evidence uses Chrome with native extension APIs and controlled loopback HTTP. Chrome/Firefox/Edge builds and shared boundary tests passed; Firefox/Edge loaded browser runtime and live provider capture were not qualified in this slice.
- Installed popup-tab qualification does not prove native toolbar popup presentation. The product requires the popup to remain open during transfer; no persistent background capture workflow was introduced.

## Owned paths

`browser-extension/capture-operation.ts`, `capture-transport.ts`, `core.ts`, `popup.ts`; `tests/popup.test.ts`, `tests/core.test.ts`, `tests/capture-transport.test.ts`, `tests/chrome-extension.test.ts`, `tests/e2e/_chrome_extension.ts`, `tests/e2e/loaded-extension.e2e.ts`; `wdio.conf.ts`. Dependencies, manifests and Firefox installation behavior are unchanged.
