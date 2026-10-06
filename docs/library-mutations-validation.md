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
