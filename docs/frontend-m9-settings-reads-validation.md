# M9 settings system and trash ownership

Status: locally qualified; final integrated CI remains M11.
Health, release status, Trash rows and the active GC plan previously had local
remote-state copies and effect fetches in SettingsPanel. Late receipts can update
a disposed/private view; failed listings can appear empty. Query now owns these
reads, consume cancellation, and exposes retry/error states. Concrete trash/GC
commands publish acknowledged results under a live session and preserve
server-side destructive-operation gates. Local state retains only user intent,
confirmation text, pending operation and purge outcome. The effect fetches and DTO setters are removed. Roll back the owner and its consumers together.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| R1 | shares system status between observers | Happy | Two observers | One health/release read each | Frontend unit | ✅ `settings-system.test.tsx` |
| R2a | retries unavailable health information | Error | Health503 then retry | Error clears after successful read | Frontend unit | ✅ `settings-panel.test.tsx` |
| R2b | retries unavailable release information | Error | Release503 then retry | Release status recovered | Frontend unit | ✅ `settings-panel.test.tsx` |
| R3 | refuses false empty trash after a failed listing | Error | Trash503 then retry | Error rather than empty state; row recovered | Frontend unit | ✅ `settings-panel.test.tsx` |
| R4 | keeps trash available when the GC read fails | Error | GC503, Trash succeeds | Restorable rows; GC actions disabled | Frontend unit | ✅ `settings-panel.test.tsx` |
| R5 | publishes confirmed restore/purge without another listing | Happy | Restore/purge ACK | Exact row removed from canonical read | Frontend unit | ✅ `settings-trash.test.tsx` |
| R6 | prevents a held trash read from resurrecting a restored row | Edge | Stale read overlaps command | No resurrection | Frontend unit | ✅ `settings-trash.test.tsx` |
| R7a | retires a held receipt on logout/disposal | Error | Lifetime ends during command | No stale publication | Frontend unit | ✅ `settings-trash.test.tsx` |
| R7b | keeps a replacement read alive after a disposed command | Edge | New read while old ACK pending | New read completes; current row retained | Frontend unit | ✅ `settings-trash.test.tsx` |
| R8a | recovers a preview claimed after the trash section loaded | Edge | Preview returns active-plan conflict | Canonical active plan loaded | Frontend unit | ✅ `settings-panel.test.tsx` |
| R8b | reports a preview conflict when no active plan can be read | Error | Conflict then null active plan | Failure remains visible | Frontend unit | ✅ `settings-panel.test.tsx` |
| R9 | preserves a newer GC observation after a held receipt | Edge | Held transition ACK after newer read | Newer plan remains | Frontend unit | ✅ `settings-trash.test.tsx` |
| R10a | requires the exact digest before requesting backup verification | Happy | Preview digest confirmation | Approval enabled only for exact digest | Frontend unit | ✅ `settings-panel.test.tsx` |
| R10b | keeps finalization unavailable before quarantine expires | Edge | Future quarantine deadline | Finalize disabled; no request | Frontend unit | ✅ `settings-panel.test.tsx` |
| R10c | aborts an active preview without issuing a destructive transition | Happy | Operator aborts preview | Aborted receipt; no delete | Frontend unit | ✅ `settings-panel.test.tsx` |
| R11a | refuses a GC command without administrator authority | Error | Member scope | No command request | Frontend unit | ✅ `settings-trash.test.tsx` |
| R11b | refuses an intent after the current trash read fails | Error | Current read403 | No command request | Frontend unit | ✅ `settings-trash.test.tsx` |
| R11c | refuses a stale gesture after permissions retire its scope | Error | Scope retired before gesture | No command request | Frontend unit | ✅ `settings-trash.test.tsx` |
| R12 | expired GC preview is non-destructive without an independent backup | Happy | Real FastAPI, trashed Model, no independent backup | Preview preserves Model; approval409; abort succeeds | Playwright | ✅ `tests/e2e-real/settings.spec.ts` |

## Actual validation

Initial two regressions failed before the cutover (6.53s). The first combined
run had 195 passes and nine failures: old tests interacted with async forms before
the controls were enabled. Their observable readiness waits were corrected.
The next combined run passed 207 tests and failed one new test which reused fresh
Query data instead of starting its intended replacement read. Explicit staleTime0
corrected that test. Final affected selection passed 15 tests in8.14s; combined
with the unchanged passing cases this qualifies209 distinct cases. No full frontend
suite was run. App/UI/domain types, lint and formatting (784 files) passed.
The real FastAPI GC flow passed1/1 (16.8s body,56.4s invocation).

Independent read errors no longer manufacture empty success. Commands cancel
stale reads, fence session/view lifetime and preserve intervening observations.
Trash receipts update membership; GC commands retain the existing server safety
contract rather than inventing a generic edit-version protocol. The quarantine
clock remains a local deadline display, not a remote-state poller. This increment
makes no performance claim. Final whole-migration qualification is M11.
