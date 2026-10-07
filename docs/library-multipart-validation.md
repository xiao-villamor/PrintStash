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



## Auxiliary editing recovery — M4 closure work

Source is qualified in `f3dc62d6`; M4 remains the only active milestone. Multipart
composition already has conflict review. Its tag dialog and cover commands still
report failures without a usable reviewed retry, and their successful field patches
can remain invisible in an older composition draft. This increment completes those
same editing contracts, without starting M5.

The tag UI receives an optional, explicit review capability: current authorized
tags plus commands to adopt them or save against that reviewed snapshot. Versions
and cache publication remain in the Multipart caller. Cover recovery shares the
existing Multipart review panel, retains its selected File/delete intent and
updates only cover fields after confirmation. An unrelated composition draft keeps
its original base when the auxiliary command required a newer reviewed version.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| A1 | retains Multipart tags after a conflicting write | Error | Card/detail tag PUT412 | Selection retained; normal Save disabled; explicit review available | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::retains Multipart tags after a conflicting write` |
| A2 | requires review after an uncertain tag write | Error | Tag PUT network error or 503 | No blind retry; selection retained | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::retains Multipart tags after a $label write` |
| A3 | retries Multipart tags against the reviewed version | Happy | Fresh GET then explicit retry | Original selection sent with exact reviewed version | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::retries Multipart tags against the reviewed version` |
| A4 | adopts current tags without another write | Happy | Conflict review followed by Use latest | Canonical detail updated; no additional PUT | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::adopts current tags without another write` |
| A5 | retains tag selection after failed review | Error | Review GET503 | No retry write enabled; selection remains | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::retains tag selection after failed review` |
| A6 | hides tag selection after denied review | Error | Review GET403/404 | Private selection hidden; no save action | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::hides tag selection after review returns %s` |
| A7 | disables tag retry after edit permission loss | Error | Fresh role view | Latest tags visible; retry unavailable | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::disables tag retry after edit permission loss` |
| A8 | requires another review after a second tag conflict | Edge | Writer races reviewed retry | Old reviewed command cannot be repeated | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::requires another review after a second tag conflict` |
| A9 | preserves the composition base after reviewed tag confirmation | Edge | Composition draft v1; tags retried at v7 | Confirmed tags visible; unrelated name retained; aggregate PUT still matches v1 | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::preserves the composition base after reviewed tag confirmation` |
| A10 | retains the selected Multipart cover for reviewed retry | Error | Cover PUT412 | Same File retried with reviewed version; no aggregate PUT | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::retains the selected Multipart cover for reviewed retry` |
| A11 | reviews uncertain Multipart cover deletion | Error | DELETE503 or lost response | Delete intent retained; no repeat until explicit review | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::reviews uncertain Multipart cover deletion after $label` |
| A12 | adopts the current cover without replacing composition input | Happy | Cover review followed by Use latest | Current cover shown; local name/base retained; no write | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::adopts the current cover without replacing composition input` |
| A13 | retains cover intent when review fails | Error | Cover review GET503 | Retry unavailable; explicit review can be repeated | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::retains cover intent when review fails` |
| A14 | hides Multipart content after denied review | Error | Review GET403/404 | Draft/private fields disappear | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::hides Multipart content after review returns %s` |
| A15 | blocks cover retry after losing write permission | Error | Review role view | Retry disabled; authorized snapshot remains readable | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::blocks cover retry after losing write permission` |
| A16 | suppresses competing cover submissions | Edge | First cover command pending | One request; aggregate save disabled | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::suppresses competing cover submissions` |
| A17 | retires a cancelled Multipart review | Edge | Cancel editor while GET held | Late review cannot reopen or replace new editor | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::retires a cancelled Multipart review` |
| A18 | suppresses retired tag editor effects | Edge | Editor/session ends while command or review pending | No late close/publication into replacement editor | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::retires a tag review when its editor is cancelled; suppresses a tag acknowledgement after session retirement` |
| A19 | preserves the composition base after reviewed cover confirmation | Edge | Draft v1; cover retried at v7 | Cover receipt visible; draft name retained; aggregate PUT matches v1 | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::preserves the composition base after reviewed cover confirmation` |
| A20 | rejects invalid Multipart editing acknowledgements | Error | Wrong aggregate, missing/stale/unsafe version | Error requiring review; no malformed success published | Frontend unit | ✅ `frontend/src/lib/api/__tests__/multipart-models.test.ts::Multipart $label acknowledgement::rejects $label instead of confirming the edit` |
| A21 | preserves confirmed auxiliary edits against late reads | Edge | Older GET resolves after tag/cover acknowledgement | Newer canonical receipt remains | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::preserves confirmed %s against an older GET` |
| A22 | recovers Multipart auxiliary editing against the real backend | Happy | Competing tags and cover edits while composition draft open | Explicit retries persist; unrelated draft cannot silently overwrite later edits | Playwright | ✅ `frontend/tests/e2e-real/multipart-models.spec.ts::recovers auxiliary conflicts without rebasing the composition draft` |
| A23 | preserves composition recovery after a rejected cover | Error | Cover validation422 followed by composition412 | Composition review retries the composition, never the rejected cover | Frontend unit | ✅ `frontend/src/components/__tests__/multipart-model-browser.test.tsx::preserves composition recovery after a rejected cover` |
| A24 | retains ordinary tags after a rejected submission | Error | Caller without review capability rejects | Selection retained; Save enabled | Frontend unit | ✅ `frontend/src/components/__tests__/entity-tags-editor.test.tsx::retains ordinary tags after a rejected submission` |
| A25 | submits ordinary tags once per pending command | Edge | Double click while write is pending | One command containing the selected tags | Frontend unit | ✅ `frontend/src/components/__tests__/entity-tags-editor.test.tsx::submits ordinary tags once per pending command` |
| A26 | suppresses a retired tag editor close callback | Edge | Editor unmounts before acknowledgement | Old acknowledgement cannot close a replacement editor | Frontend unit | ✅ `frontend/src/components/__tests__/entity-tags-editor.test.tsx::suppresses a retired tag editor close callback` |

Required acceptance: every row covered, retained composition/tag/cover contracts
passing, app/UI/domain types and lint/format passing, and the real browser flow
verified. Rollback reverts the recovery UI and caller contracts together, retaining
backend conditional writes and their already documented limitation on physical
restore. The old toast-only retry mechanism is removed for Multipart auxiliary
conflicts; no second Query owner or automatic overwrite is introduced.

### Auxiliary editing qualification — 2026-10-07

- Before the corrections, 32 malformed-acknowledgement cases failed with 23
  existing wire controls passing. Eleven tag recovery cases and eleven cover
  recovery cases separately failed against the prior behaviour. An additional
  rejected-image → composition-conflict case reproduced dispatching the wrong
  retained command; the rejected cover intent is now cleared.
- Integrated qualification: **235/235 in seven files, 32.45 s**, covering the
  editor, optional shared tag review, conditional wire receipts, Multipart Query
  owner, dependency boundaries and locale coverage. Five focused lifecycle/read
  race cases also passed. App/UI/domain typechecks, full lint and full formatting
  check (**749 files**) passed. Initial invalid fixture typing and a conditional
  test assertion were corrected; their failure logs are retained.
- Real Chromium/FastAPI/SQLite headline: **1/1, 14.1 s** (launch/run 1.1 min).
  It observes actual 412 responses for tags and cover, reviews/retries each,
  checks exact `If-Match` values and persistent tags/cover, then proves the
  unrelated composition draft still conflicts at its original version.
- Existing mock-browser flows: **2/3 passed initially** (23.0 s); the remaining
  composition lifecycle fake returned its old version after PUT. Its receipt now
  increments exactly as the real backend does; that flow passed **1/1, 14.1 s**
  (run 16.5 s). The error snapshot is retained. No product guard was weakened.
  Three applicable suite-hygiene checks passed (1.44 s); the whole-suite nesting
  check remains deferred with the already recorded M5 snapshot spec, not reported
  as green here.
- No performance improvement is claimed. This closes the auxiliary editing
  increment, not M4: grid movement preconditions, restored editing-token identity
  and the milestone's final acceptance/removal audit remain.

The shared tag editor's review capability is opt-in. It owns the retained
selection, pending gesture and editor lifetime; the Multipart caller supplies
an authorized snapshot and commands bound to that snapshot. No generic form
framework, extra remote cache, or automatic retry is introduced. Ordinary callers
keep their existing error presentation. Multipart receipt validation is at the
endpoint client; successful fixtures now acknowledge an advanced version.
