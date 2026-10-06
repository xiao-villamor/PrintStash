# Quick Model tag conflict recovery (M4)

Status: bounded checkpoint qualified; the wider M4 migration remains open. The quick dialog already sends the
captured Model edit version, but a conflicting batch receipt becomes a generic
error with no route to review current tags. The main Model editor has a review
contract; this dialog must preserve its own draft and offer the same explicit
choice without silently retrying a newer version.

Scope: ModelTagsDialog and its mirror, existing library tag browser headline,
and this document. No backend or batch wire changes. Retain case-insensitive
canonical names, local editing, and confirmed version publication. A review reads
the authorized Model; submitting a reviewed draft uses that exact version and
computes the delta against the reviewed tags. Newer edits still conflict. A failed
or retired read never authorizes another write. Modal dismissal during Save is
blocked, and retired/unmounted continuations cannot publish callbacks or feedback.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | publishes the confirmed tag version | Happy | acknowledged batch version | caller receives exact version | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 2 | assigns an existing tag | Happy | existing suggestion selected | conditional delta sent | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 3 | creates a new tag through the assignment | Happy | new name selected | add payload contains name | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 4 | removes an assigned tag | Happy | existing tag removed | remove payload contains name | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 5 | keeps save disabled without changes | Edge | unchanged selection | no enabled Save | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 6 | reuses the canonical name for a case-insensitive match | Edge | alternate case input | canonical suggestion reused | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 7 | discards pending tags when cancelled | Edge | unsaved draft closed/reopened | original tags restored | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 8 | keeps pending choices after a server error | Error | refused command | local draft retained | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 9 | requires explicit review after a conflict | Error | batch edit_conflict | draft retained, Save blocked, review available | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 10 | saves the reviewed draft against the exact reviewed version | Happy | latest tags/version read | explicit retry delta uses latest base; receipt published | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 11 | adopts the latest tags only by explicit choice | Happy | user chooses latest after review | local draft replaced, no write | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 12 | keeps a failed review recoverable | Error | review GET503 | draft retained, no enabled overwrite | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 13 | prevents retry after reviewed edit permission loss | Error | latest Model view role | draft cannot be saved | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 14 | ignores acknowledgement after disposal | Edge | held write, unmount | no onSaved or onClose callback | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 15 | keeps a pending save open on Escape | Edge | held write, Escape | modal stays open | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 16 | requires review after an unconfirmed write | Error | network/5xx outcome | draft retained, no blind retry | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 17 | retires private tag UI on session change | Edge | session replaced while open | no private draft remains rendered | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 19 | hides the private draft after a denied review | Error | review GET403/404 | private fields absent, explicit retry available | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 20 | aborts review when the dialog closes | Edge | held review GET, Cancel | native signal aborted; late body does not publish review | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 21 | freezes selection while save is pending | Edge | held batch receipt | draft inputs disabled; confirmed original selection published | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 22 | requires a new review when the reviewed base changes again | Error | second conditional receipt conflicts | no automatic retry; old review discarded | Frontend unit | ✅ components/__tests__/model-tags-dialog.test.tsx |
| 18 | completes quick-tag conflict recovery in the browser | Happy | another editor changes tags | review, explicit retry, persisted result | Playwright | ✅ tests/e2e-real/tags.spec.ts::reviews a conflicting quick-tag draft before replacing current tags |

Rollback: revert the dialog checkpoint as one unit; conditional batch API remains
unchanged. This restores the known missing-recovery defect and is not a claim of
safe conflict UX. Provenance/source-cover and Multipart auxiliary editors remain
separate M4 work.


## Source inspection before implementation

Read the complete `frontend/src/components/model-tags-dialog.tsx` and its existing
8-case mirror. Read the calling tag-dialog lifetimes in ModelBrowser and Model
Detail: both key each opening by entity and dialog session. The existing dialog
captures a base version, sends it through batch tags, and publishes the returned
version; preserve those contracts. The new review supplies a missing recovery
choice, rather than replacing the conditional writer.

Also read the complete `components/model-detail/source-tab.tsx`,
`lib/api/provenance.ts`, `types/provenance.ts`, and
`features/library/model-detail.ts`. These reads establish separate remaining
source findings, not executed reproductions: SourceTab owns an uncancelled copied
provenance result, treats failure as empty, and its override/cover/apply-to-Model
commands do not pass the existing backend edit preconditions. SourceCover turns
all read errors into absent-cover presentation. Their migration remains open;
quick-tag completion will not close those surfaces or all of M4.


First qualification: before production edits the existing 8 cases passed and all
9 new regressions failed (12.41s), showing missing conflict review, unconfirmed
write recovery, disposal/escape handling and session UI retirement. After the
correction the complete dialog mirror passed **17/17 in 7.59s**. No assertion,
timeout or environment was weakened. Browser persistence and final static checks
remain pending; this is not yet an accepted checkpoint.

Expanded qualification: two confirmed-denial cases first failed with the private
Model name/draft still rendered after GET403/404. The dialog now immediately
replaces those fields with a generic Retry surface. Full mirror **22/22 passed
in 22.75s** with native abort-on-close, frozen pending selection and repeated
conflict review. Full app/UI/domain typecheck and frontend lint passed; the three
owned source/test files were formatted. No timeout was changed. The browser
headline and final repository hygiene check remain pending.


Closing qualification: the real-backend quick-tag conflict headline passed in
**12.3s** (run 1.3m including a fresh database). It makes a competing conditional
Model edit through the actual API, retains the quick-tag draft on conflict,
reviews current tags, asserts the exact reviewed version in the retry request,
and reads persisted tags and the advanced version from the server. The normal
browser/startup limits remain unchanged. It reuses the official BG-code binary
built by the previous saved-view gate through the existing supported environment
option. Root's integrated six-file gate also passed **89/89 in 23.98s**, including
suite hygiene and locale coverage; full integrated app/UI/domain types passed.

All 22 requirement rows are now covered (the denied-review row exercises both
403 and 404). This checkpoint removes generic dead-end conflict handling in the
quick editor; no new state framework, backend contract or dependency was added.
Source provenance/covers and Multipart auxiliary editors remain unqualified.
