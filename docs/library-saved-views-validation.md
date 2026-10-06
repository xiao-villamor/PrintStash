# Saved-view ownership validation

The library saved-view list is a private, account-scoped server projection. A feature owner provides its Query read and commands; forms retain their own drafts. The key includes the session incarnation and account identifier. Commands serialize within that key, cancel older reads before publication, and cannot publish after session retirement. POST and PATCH return a complete `SavedViewRead`; DELETE returns 204. Acknowledgements publish directly after retiring older reads. The existing URL filter mapping, library modes, default sort and backend authorization contract remain unchanged.

The list endpoint depends on `require_user`, which rejects an inactive or invalid session with 401. It has no 403/404 outcome; identity-specific endpoints use 404 for an inaccessible or absent saved view. A confirmed 401 uses the shared transport session retirement, whereas a transient 503 can retain the last authorized list with recovery UI.

This checkpoint changes no backend schema or API. Public saved-view DTOs contain names, filters, identifiers and timestamps; they contain no credentials.

## Coverage matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | shows loading while the first list is pending | Edge | Deferred list | Loading is visible; empty message absent | Frontend unit | ✅ `src/components/__tests__/saved-view-selector.test.tsx::shows loading while the first list is pending` |
| 2 | offers retry after a failed list | Error | List returns 503 | Recovery message replaces false empty | Frontend unit | ✅ `src/components/__tests__/saved-view-selector.test.tsx::offers retry after a failed list` |
| 3 | recovers the list after retry | Happy | Failed list then successful retry | Authoritative saved views become selectable | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::recovers the list after retry` |
| 4 | shares the authorized list across observers | Happy | Two readers in one session | One GET; both show the list | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::shares the authorized list across observers` |
| 5 | cancels a read when its last observer leaves | Edge | Deferred read then unmount | Transport signal aborted | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::cancels a read when its last observer leaves` |
| 6 | hides private views when the session retires | Edge | Loaded list then logout | Prior names absent immediately | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::hides private views when the session retires` |
| 7 | isolates a replacement account | Edge | Account switches while read pending | Only replacement account views appear | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::isolates a replacement account` |
| 8 | ignores a retired mutation acknowledgement | Edge | Deferred write then session change | No retired row or success published | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::ignores a retired mutation acknowledgement` |
| 9 | publishes a created view from its acknowledgement | Happy | Create returns complete DTO | Returned row appears without another GET | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::publishes a created view from its acknowledgement` |
| 10 | publishes an updated view from its acknowledgement | Happy | Update returns complete DTO | Returned row replaces prior row without GET | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::publishes a updated view from its acknowledgement` |
| 11 | removes a deleted view after acknowledgement | Happy | Delete returns 204 | Removed identity absent without GET | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::removes a deleted view after acknowledgement` |
| 12 | rejects a read older than the write acknowledgement | Edge | Deferred refresh overlaps write | Late list cannot replace acknowledged data | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::rejects a read older than the write acknowledgement` |
| 13 | retains the list after a failed mutation | Error | Write returns 409 or 503 | Existing rows remain unchanged | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::retains the list after a failed mutation` |
| 14 | preserves a newer create name after acknowledgement | Edge | Type a new name during create | New draft remains open with its text | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::preserves a newer create name after acknowledgement` |
| 15 | preserves a reopened create dialog after acknowledgement | Edge | Close and reopen during create | New dialog remains open | Frontend unit | ⏭️ N/A — existing create dialog blocks dismissal while saving; that contract is preserved |
| 16 | preserves a newer rename draft after acknowledgement | Edge | Edit name during rename | New text remains in open dialog | Frontend unit | ✅ `src/components/__tests__/saved-view-selector.test.tsx::preserves a newer rename draft after acknowledgement` |
| 17 | preserves a reopened rename dialog after acknowledgement | Edge | Close and reopen during rename | New dialog remains open | Frontend unit | ✅ `src/components/__tests__/saved-view-selector.test.tsx::preserves a reopened rename dialog after acknowledgement` |
| 18 | retains rename input after failure | Error | Rename rejects | Dialog remains open; rejection handled | Frontend unit | ✅ `src/components/__tests__/saved-view-selector.test.tsx::retains rename input after failure` |
| 19 | retains delete confirmation after failure | Error | Delete rejects | Confirmation remains open; rejection handled | Frontend unit | ✅ `src/components/__tests__/saved-view-selector.test.tsx::retains delete confirmation after failure` |
| 20 | requires confirmation before deleting | Edge | Open delete confirmation | No DELETE before confirmation | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::asks before deleting a view` |
| 21 | does not read views without an account | Edge | Signed out | No private read | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::does not read views without an account` |
| 22 | forwards list cancellation to the transport | Edge | Caller aborts pending list | Request rejects cancellation | Frontend unit | ✅ `src/lib/api/__tests__/saved-views.test.ts::forwards list cancellation to the transport` |
| 23 | saves the current URL filters | Happy | Selected filters and mode | POST contains current filters | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::saves the filters that are actually on screen` |
| 24 | restores the saved library mode | Happy | Saved Multipart Sets view | URL and selected presentation match | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::restores the saved library mode` |
| 25 | rejects a queued command after session retirement | Edge | Write gesture then synchronous retirement | No write reaches the next session | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::rejects a queued command after session retirement` |
| 26 | serializes writes within the authorized list | Edge | Two writes while first response is deferred | Second write waits; latest acknowledgement remains | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::serializes writes within the authorized list` |
| 27 | rejects a command before the list is loaded | Edge | Create while initial list is deferred | No POST or fabricated partial list | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::rejects a command before the list is loaded` |
| 28 | rejects an acknowledgement for a different identity | Error | PATCH returns another identifier | Prior list retained; command fails | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::rejects an acknowledgement for a different identity` |
| 29 | preserves a reopened delete confirmation after acknowledgement | Edge | Dismiss pending delete with Escape; open another confirmation | New confirmation stays open | Frontend unit | ✅ `src/components/__tests__/saved-view-selector.test.tsx::preserves a reopened delete confirmation after acknowledgement` |
| 30 | offers no saved views to a signed-out visitor | Edge | Signed out | No saved-view control | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::offers no saved views to a signed-out visitor` |
| 31 | rejects an empty write acknowledgement | Error | Create answers 204 instead of a complete DTO | Prior list retained; command fails | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::rejects an empty write acknowledgement` |
| 32 | immediately retires the private create draft with its session | Edge | Session retires while create modal is open | Private name is immediately absent, without exit-animation retention | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::immediately retires the private create draft with its session` |
| 33 | saves and manages a view against the real backend | Happy | Save tag filters, reload, apply, rename, duplicate, delete | Persisted views restore the canonical URL and list acknowledgements remain usable | Playwright | ✅ `tests/e2e-real/saved-views.spec.ts::save current filters as a view, apply it restores the canonical URL` |
| 34 | retires the rename draft after a denied list refresh | Error | Cached list plus open rename, refresh returns 401 | Private names/draft removed; no replacement private GET | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::retires the rename draft after a denied list refresh` |
| 35 | retains the last list after a transient read failure | Error | Cached list, refresh returns 503 | Last authorized names remain visible | Frontend unit | ✅ `src/features/library/__tests__/saved-views.test.tsx::retains the last list after a transient read failure` |

## Validation

- Focused saved-view component/API/owner checks: 49 passed (4 files; unrelated cases deselected).
- Warm-cache denial/transient-failure checks: 2 passed (2 files; unrelated cases deselected).
- Owner/API plus dependency boundaries, suite hygiene and locale gates: 109 passed (6 files).
- Frontend, UI and domain TypeScript checks: passed.
- Frontend lint: passed, no warnings or errors.
- Whole frontend format check: passed, 737 files; final owned-file format check passed, 11 files.
- Existing real-backend saved-view lifecycle: initial standard startup hit its 240-second bound after the official converter build and migrations; no browser test ran. A second launch reused the verified official converter through the supported configuration option with unchanged startup/test bounds: the real saved-view lifecycle passed (25.4 seconds; 1.8 minutes including startup).

The first red pass exposed read recovery, ignored cancellation, rename draft retirement and unhandled command rejection defects. A new create-draft test initially counted an unrelated POST; its request assertion was narrowed to the saved-view endpoint before the green run. A deferred delete-confirmation red case demonstrated that an older acknowledgement could close a newer confirmation; dialog incarnations now fence that completion. The open create-draft session-retirement regression already passed through the existing page/session boundary. A separate deferred-write red case demonstrated concurrent writes racing; writes now serialize within the private list scope.

No global coverage claim is made: this bounded checkpoint uses behavior assertions and the existing browser lifecycle. Full repository integration and required remote CI remain the parent task's responsibility.
