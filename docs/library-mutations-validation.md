# Confirmed library mutations

Requirements recorded before implementation. This slice replaces component star
overrides with confirmed Query publication. General Model/Multipart conditional
editors and history anchors remain separate M4/M5 work.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | publishes a confirmed star across loaded browse scopes | Happy | same Model in two scopes | both show confirmed state | Frontend unit | ✅ `src/features/library/__tests__/mutations.test.tsx::publishes a confirmed star across loaded browse scopes` |
| 2 | removes an unstarred favorite only after confirmation | Happy | pending unstar in Favorites | remains until success, then disappears | Frontend unit | ✅ `src/features/library/__tests__/mutations.test.tsx::removes an unstarred favorite only after confirmation` |
| 3 | keeps a favorite when unstar fails | Error | rejected unstar | item stays starred with actionable error | Frontend unit | ✅ `src/features/library/__tests__/mutations.test.tsx::keeps a favorite when unstar fails` |
| 4 | rejects a late page after a confirmed star | Edge | pending continuation returns old state | confirmed card is not overwritten | Frontend unit | ✅ `src/features/library/__tests__/mutations.test.tsx::rejects a late page after a confirmed star` |
| 5 | does not publish a star into another session | Error | identity changes during response | new session cache unchanged | Frontend unit | ✅ `src/features/library/__tests__/mutations.test.tsx::does not publish a star into another session` |
| 6 | isolates equal numeric identifiers across subject kinds | Edge | Model and Multipart share id | only selected kind changes | Frontend unit | ✅ `src/features/library/__tests__/mutations.test.tsx::isolates equal numeric identifiers across subject kinds` |
| 7 | removes a confirmed favorite from the displayed grid | Happy | pending unstar with stale GET fixture | card stays pending and disappears after confirmation | Frontend unit | ✅ `src/components/__tests__/model-grid.test.tsx::removes a confirmed favorite from the displayed grid` |
| 8 | stars either supported subject kind | Happy | Model or Multipart | confirmed true state in browse | Frontend unit | ✅ `src/features/library/__tests__/mutations.test.tsx::stars either supported subject kind` |
| 9 | rejects a queued gesture after session retirement | Error | retirement before mutation preparation | no write sent in new session | Frontend unit | ✅ `src/features/library/__tests__/mutations.test.tsx::rejects a queued gesture after session retirement` |
| 10 | lights the star before the server answers | Happy | pending command | pending icon updates, duplicate action disabled | Frontend unit | ✅ `src/components/__tests__/model-card.test.tsx::lights the star before the server answers` |
| 11 | removes a favorite after browser confirmation | Happy | pending successful unstar | card stays until acknowledgement then disappears | Playwright | ✅ `tests/e2e/vault.spec.ts::removes a favorite after browser confirmation` |

Validation: 226 tests passed across grid/card/Multipart/mutation-owner files;
4 focused Chromium cases passed (confirmed favorite removal, mixed append,
empty-page continuation, protected thumbnail admission). Lint reported no
warnings; app/UI/domain typechecks and format check passed. New owner tests first
failed module resolution, which is not a behavioral reproduction. The actual grid
regression then reproduced the retained favorite after acknowledgement before the
card was migrated, and passed after migration.

The card's pending icon remains responsive; persisted state comes from confirmed
Query data. Removed component-local star overrides and the Multipart star's
redundant page refresh. The owner captures session identity at the gesture, fences
both cancellation awaits, and retires stale reads again before publication.
Favorites membership changes only after confirmation. General conditional editing,
anchor preservation and controlled refresh remain incomplete M4/M5 work.

Required Model/Multipart edit_version response fields now match backend schemas;
shared builders and existing typed fixtures include that field. This is contract
preparation, not a claim that all writers already send preconditions.

The two grid list thumbnail containers now use viewport admission, complementing
the M6 card/Cover changes; the integrated functional browser test decodes the
visible image while asserting the four-request bound and distant-image deferral.
No performance timing claim is made from that functional test.

## M4 confirmed-removal reading position

The parent matrix requires real-backend confirmation with a preserved reading
position, beyond the earlier single-card mock assertion. This is the own-mutation
contract in M4, not Back/Forward restoration from M5.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| F1 | preserves the reading position after a confirmed favorite removal | Happy | Scrolled Favorites grid; actual server commit precedes a held acknowledgement | Pending card remains; confirmation removes it without moving the neighboring reading card | Playwright real | ✅ `tests/e2e-real/favorites.spec.ts::preserves the reading position after a confirmed favorite removal` |
| F2 | applies the promoted anchor only after the confirmed card leaves the DOM | Edge | Confirmation precedes the displayed snapshot update | Neighbor keeps its saved offset; no premature scrolling | Frontend unit | ✅ `src/features/library/__tests__/reading-position.test.tsx::applies the promoted anchor only for the active session after DOM removal` |
| F3 | ignores a confirmed removal after session retirement | Error | Gesture from the retired session completes | Current scroll position is untouched | Frontend unit | ✅ `src/features/library/__tests__/reading-position.test.tsx::applies the promoted anchor only for the retired session after DOM removal` |
| F4 | retains the promoted anchor across a queued unchanged scroll event | Edge | Old scroll event arrives between confirmation and DOM removal | Neighbor restores exactly once after removal | Frontend unit | ✅ `src/features/library/__tests__/reading-position.test.tsx::applies the promoted anchor only for the active with queued scroll session after DOM removal` |

The real-browser probe isolates the confirmation interval: focusing the clicked
button can scroll before acknowledgement, so that separate movement is excluded.
A 346 px neighbor displacement remained after confirmation. Earlier harness
attempts used the wrong star verb, included the return URL in an entity selector,
or measured a different interval; they are not product reproductions.

The reading-position owner now records a card action against its associated item,
including buttons beside the link. It remembers the gesture's reading metadata,
observes the existing confirmed-removal neighbor promotion, and adjusts the
mounted scroll container only after the removed card leaves the DOM. An unchanged
queued scroll event cannot replace the promotion before it is applied. Real
scrolling, session retirement and entry/layout changes still retire old intent.
There is no new event bus, fetch, cache, timer, animation or history entry.

Qualification: the active-session owner case failed before the fix while its
retired-session control passed. A second queued-scroll case failed before its
correction. Final owner/navigation/mutation run: **35 passed in 6.08 s**. Real
Chromium after the final correction: **1 passed in 11.4 s** (1.5 min including
startup). App/UI/domain types and frontend lint passed. This closes the F1–F4
behaviours; M4 itself awaits the wider backend gate's outstanding failure.
Rollback this scroll adjustment and its card-action capture together; retain
confirmed Query publication and the existing navigation metadata contract.

Current acceptance: the backend expectation failure was corrected and qualified;
[M4 is locally closed](frontend-m4-closure-validation.md). The earlier pending-gate
paragraph records the Favorites checkpoint, not an unresolved mutation defect.
