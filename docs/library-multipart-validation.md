# Multipart editing ownership

M4/M8 increment. `MultipartModelDetailPage` had a Query snapshot plus a persisted local server copy plus a draft. An executed regression showed the local copy hiding future authorized updates; wire regressions showed writes omitting the conditional contract. Ownership is now the canonical Multipart Query, a local frozen draft, and an explicit review snapshot after a conflict/unknown outcome. Successful cover/tag edits can advance only the draft version they actually matched, using the exact acknowledgement. The most recent authorized role controls write admission, never an old role inside the draft.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | sends the editor's Multipart version | Happy | Composition/tags/cover mutation based on v7 | conditional-v1 and exact If-Match | API wire | ✅ |
| 2 | aborts an obsolete Multipart read | Edge | Reader disposed while pending | Transport aborted | API wire | ✅ |
| 3 | reflects an authorized Multipart refresh | Happy | New canonical detail receipt | Updated overview visible | Component | ✅ |
| 4 | preserves the Multipart draft base during refresh | Edge | Draft v1; remote v7 arrives | Draft retained; PUT matches v1 | Component | ✅ |
| 5 | preserves a Multipart draft on edit conflict | Error | PUT412 | Name and existing part retained; Save blocked | Component | ✅ |
| 6 | retries Multipart editing only after explicit review | Happy | Review latest v7 | Explicit retry uses v7 and original draft | Component/browser | ✅ |
| 7 | adopts reviewed Multipart values without a write | Happy | User chooses latest | Draft replaced; no additional PUT | Component | ✅ |
| 8 | keeps a Multipart draft when review fails | Error | GET503 after conflict | Draft retained; Save still blocked | Component | ✅ |
| 9 | prevents a Multipart retry after losing edit permission | Error | Latest authorized role view | Retry disabled even if draft role was admin | Component | ✅ |
| 10 | retires an older Multipart read before publishing a write | Edge | Old GET held; PUT acknowledged | Confirmed composition survives old result | Component | ✅ |
| 11 | suppresses Multipart publication after session retirement | Edge | Old acknowledgement returns | No private cache or feedback recreated | Component | ✅ |
| 12 | retains a Multipart draft after an unknown save outcome | Error | PUT response lost | Review required; no blind retry | Component | ✅ |
| 13 | carries an own cover acknowledgement into the draft version | Happy | Cover matches draft base; edit_version jumps | Subsequent aggregate save uses exact acknowledged version | Component | ✅ |
| 14 | freezes the tag command at dialog opening | Edge | Background update while tags editor open | Save uses original draft base | Component | ✅ |
| 15 | retains authorized Multipart content through transient read failure | Error | Background GET503 | Draft retained with Retry | Component | ✅ |
| 16 | hides denied Multipart content | Error | Background GET403/404 | Private overview removed | Component | ✅ |
| 17 | publishes confirmed card tags into the canonical Multipart detail | Edge | Card tags ACK v9; prior detail cached | Opening detail uses confirmed tags/version | Component | ✅ |
| 18 | ignores a publication after its detail view is unmounted | Edge | Old route completion | Detail cache unchanged | Feature component | ✅ |
| 19 | preserves a newer Multipart receipt against an older acknowledgement | Edge | v4 cached; v2 ACK | v4 remains | Feature component | ✅ |
| 20 | requires review after a malformed Multipart acknowledgement | Error | HTTP200 missing version | Draft retained; explicit review; no success | Component | ✅ |

The initial sixteen requirements were recorded before the implementation. The additional card-cache and malformed-receipt regressions were added while reviewing the integration, executed red, then fixed. Existing guide/member/cover behaviour remains covered by its original mirrors. The shared tag dialog captures selection and command together at opening, including during lazy loading; refreshed parent data cannot silently rebase that command.

## Validation and boundaries

- Initial baseline: 64 component/API tests passed. The first eight selected regressions failed (four UI behaviours and four conditional wire writers). The tag-command refresh regression, card-to-detail receipt regression and malformed-receipt regression also failed before their fixes.
- Final focused run: 114 tests across Multipart component, API, feature owner, shared tag dialog and Query facade passed (24.91 seconds). The component/API/owner/dialog subset passed 89 tests before the two late regressions were added.
- Chromium: all three Multipart scenarios passed (25.6 seconds): explicit conflict review, mobile editor controls, and create/edit/list/delete while retaining member Models. The first run passed the first two but exposed a historical mock tied to the retired separate Multipart list endpoint. The scenario now supplies the same created aggregate through the authoritative browse endpoint and expects M2's canonical URL. No timeout increase or weakened product assertion.
- Full app/UI/domain typecheck, full format check (697 files), and four repository hygiene assertions passed. Lint identified one obsolete type import after facade delegation; it was removed and lint rerun.
- Successful composition, cover and tag requests use required versions and raw session-fenced transport. The detail owner cancels the prior exact read before publication; unrelated projections refresh without immediately replacing the acknowledged detail. Card tag acknowledgements update that same detail cache.
- Guide acknowledgements patch current guide fields without overwriting concurrent local form input; removed/retired views suppress late publication and feedback. Detail state is keyed by entity id.

This is an incremental M4/M8 checkpoint, not completion of conditional editing across every UI. Tag and cover failures still use their existing error surfaces; dedicated conflict/unknown-outcome recovery for these auxiliary editors remains a named follow-up. Model provenance/source-cover writers and quick Model tags remain outstanding. The legacy Query facade still delegates to the feature options until M10 removes compatibility seams. Integer entity versions can be reused across physical backup restore; the separate browse authority epoch does not change that editing-token limitation.

