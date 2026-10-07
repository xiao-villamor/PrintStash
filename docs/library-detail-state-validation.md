# Model detail remote-state ownership

Increment M4/M8. The canonical `models/:id` Query owns the authorized Model. Local state owns an editing draft and its captured edit version. A derivative notice requests a shared read; confirmed writes cancel older reads before publication. This removes the route fetch, detail copy, and event callback copy as competing owners. Printer-history projections use separately cancellable queries. Multipart and provenance remain separate follow-ups.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | shows a refreshed authorized Model | Happy | Cache receives a newer Model | Detail title changes | Component | ✅ |
| 2 | preserves the editing base across a background refresh | Edge | Draft v1; refresh v4 | Draft retained; write still conditional on v1 | Component | ✅ |
| 3 | keeps a confirmed edit after an older read finishes | Edge | Held old GET; confirmed PATCH v4 | New title survives old response | Component | ✅ |
| 4 | retries a failed initial read without navigation | Error | GET500 then success | Retry renders Model | Component | ✅ |
| 5 | hides a cached Model after access is denied | Error | Existing Model; refetch403 | Private title disappears | Component | ✅ |
| 6 | retains displayed Model during a transient read failure | Error | Existing Model; refetch500 | Title retained with retry | Component | ✅ |
| 7 | retires a pending read when the route closes | Edge | GET held then unmount | HTTP signal aborted | Component | ✅ |
| 8 | coalesces settled derivative notices into one read | Edge | Multiple ready/resync notices during held read | Single shared request; eventual title visible | Component | ✅ |
| 9 | ignores a confirmed publication after session retirement | Edge | Command acknowledgement after retirement | No private cache recreation | Component | ✅ |

| 10 | preserves a newer Model when an older acknowledgement arrives | Edge | Cache v4; acknowledged response v2 | Current name/version retained; publication rejected | Component | ✅ |
| 11 | aborts an active Model printer files read | Edge | Caller aborts request | HTTP aborted; no result delivered | API wire | ✅ |
| 12 | publishes a confirmed print after cancelling an obsolete history read | Edge | Pending history GET then manual print acknowledgement | Confirmed print remains | Component | ✅ |
| 13 | suppresses revision feedback after the session changes | Edge | Held revision request then logout | No success message or Model callback | Component | ✅ |
| 14 | waits for confirmed Model publication before revision success | Edge | Publication pending after server acknowledgement | Success delayed until publication | Component | ✅ |

| 15 | recovers a failed Model read without reloading the route | Error | Initial GET503 then endpoint available | Retry restores detail on the same URL | Chromium | ✅ |

| 16 | suppresses revision success if publication crosses a session change | Edge | Server acknowledged; publication held; logout | No success message for retired command | Component | ✅ |

This matrix precedes tests. No measured performance improvement is claimed. Existing revision/files/source command families will be audited for callback lifetime separately; moving the Model owner alone does not complete conditional editing.

## Qualification

Before implementation, six new route/detail regressions failed: cache publication was ignored, held route reads were not cancelled, initial failures had no Retry, and cached data had no authoritative denial/retry treatment. The draft-base regression already passed and preserves an existing design. After implementation the Model detail, revision/source tabs and transport mirrors passed **221 tests in 18 files** with two Vitest workers. An earlier wider concurrent run had 202 passes and one five-second timeout during draft entry; the bounded rerun passed without increasing timeouts. Type/lint fixes were required and are qualified below before checkpointing.

Manual review for this increment: `features/library/model-detail.ts`; route `pages/model-detail.tsx`; `components/model-detail/client-view.tsx`, `index.tsx`, `use-derivative-refresh.ts`, `use-revision-updater.ts`; corresponding complete test mirrors; Model HTTP detail/printer-file reader and conditional writer; supporting Model print history callback and revision/files writer paths. This is a focused slice, not a claim of complete manual review of every Model tab.

Removal: route-local Model/error state, detail-local server Model, duplicate derivative callback fetch, optimistic detail star copy, printer history/file fetch effects. Metadata and composition drafts remain local by design. Publication consumes exact server versions; it never invents a version increment.

Historical checkpoint remainder: provenance/Multipart conditional writers, ambiguous mutation recovery and source/revision lifetime qualification were subsequently consolidated in [M4 acceptance](frontend-m4-closure-validation.md) and the source recovery matrices. M8 reconciles current detail/child assertions in [its record](frontend-m8-validation.md). The remaining legacy transport invalidation bridge and compatibility facades retain their explicit M10 deadline; this earlier checkpoint does not close them.

The first full Chromium detail run passed 13/14; the cached-preview test stopped at its obsolete expectation that Back returns a bare `/`. M2 intentionally canonicalizes that history entry to `/?type=all&sort=date-desc`. The test now captures the canonical entry and requires exact restoration, retaining both preview assertions. This was an assertion migration, not evidence of a viewer-loading failure. The corrected test is rerun before checkpointing.

Final static qualification: app/UI/domain TypeScript passed, oxlint passed, oxfmt checked 692 files, and diff whitespace check passed. The corrected cached-preview Chromium case passed (8.5s); the other 13 cases had passed in the preceding full run. A strengthened post-publication session regression was red: an awaited callback could cross logout before the success toast. It now checks the incarnation again after publication; its test waits for command completion rather than making a premature absence assertion. That focused rerun is recorded below.

Post-publication retirement fix: **27 tests in three files passed**, including the strengthened awaited-callback regression, revision editor behaviours and repository hygiene.
