# Model upload workflow ownership

Problem and outcome: Model upload execution currently lives inside `UploadModal`, even though accepted uploads intentionally outlive that form. Individual HTTP and TaskCenter operations retire their own sessions, but the bulk loop catches an aborted child and may start the next file under a replacement session. The bulk finally callback is also unguarded. Source-confirmed hypothesis; behavioral reproduction precedes production edits.

The next increment gives the existing single/mesh-plus-G-code/bulk workflow one captured session and a module in `lib/model-upload-workflow.ts`. The form owns selections and creates the single-upload task identity for its onboarding callback; the workflow owns bulk task creation, sequential files, linked jobs, completion and publication. Artifact upload still owns bytes, pause/resume and server upload sessions. TaskCenter remains the durable Job progress owner; no second polling loop or remote cache is introduced. Closing the form does not cancel accepted same-session work. Session retirement stops any remaining dispatch and publication.

Manifest: new workflow module, UploadModal, its existing uploading test mirror, this document. No backend or upload wire change. Rollback reverts the owner extraction as a unit while retaining the required session fences. Remove the component-local waitForJob/runUploadTask/runBulkUpload implementations.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | uploads the file the user chose | Happy | Select one mesh | Artifact request contains selected filename/model name | Frontend unit | ✅ `src/components/__tests__/upload-modal/uploading.test.tsx::uploads the file the user chose` |
| 2 | files it in the collection the user chose | Happy | Select target collection | Artifact request contains selected target | Frontend unit | ✅ `src/components/__tests__/upload-modal/uploading.test.tsx::files it in the collection the user chose` |
| 3 | refreshes the vault once the job lands | Happy | Successful upload/Job | Completion callback fires after terminal success | Frontend unit | ✅ `src/components/__tests__/upload-modal/uploading.test.tsx::refreshes the vault once the job lands` |
| 4 | does not refresh the vault when the job failed | Error | Failed Job | No successful completion publication | Frontend unit | ✅ `src/components/__tests__/upload-modal/uploading.test.tsx::does not refresh the vault when the job failed` |
| 5 | goes in as a slicer artifact rather than a mesh | Happy | G-code alone | Artifact request purpose is G-code | Frontend unit | ✅ `src/components/__tests__/upload-modal/uploading.test.tsx::goes in as a slicer artifact rather than a mesh` |
| 6 | uploads the mesh first | Happy | Mesh plus G-code | First request purpose is model | Frontend unit | ✅ `src/components/__tests__/upload-modal/uploading.test.tsx::uploads the mesh first` |
| 7 | links the slice to the mesh it came from | Happy | Mesh terminal Job and model hash | G-code request contains model source hash | Frontend unit | ✅ `src/components/__tests__/upload-modal/uploading.test.tsx::links the slice to the mesh it came from` |
| 8 | queues one job per file | Happy | Two bulk files | Exactly two sequential upload intents | Frontend unit | ✅ `src/components/__tests__/upload-modal/uploading.test.tsx::queues one job per file` |
| 9 | keeps going after a file the vault refused | Error | First file fails in same session | Second file still starts | Frontend unit | ✅ `src/components/__tests__/upload-modal/uploading.test.tsx::keeps going after a file the vault refused` |
| 10 | refreshes the vault once, after the whole queue | Happy | Two completed bulk files | One final publication | Frontend unit | ✅ `src/components/__tests__/upload-modal/uploading.test.tsx::refreshes the vault once, after the whole queue` |
| 11 | retires remaining bulk files during a held transfer | Edge | Session changes while first chunk is held | No second artifact POST, no old completion publication, replacement task list stays empty | Frontend unit | ✅ `src/components/__tests__/upload-modal/uploading.test.tsx::retires remaining bulk files during a held transfer` |
| 12 | retires remaining bulk files during a held job | Edge | Session changes while first Job read is held | No second artifact POST or old completion publication | Frontend unit | ✅ `src/components/__tests__/upload-modal/uploading.test.tsx::retires remaining bulk files during a held job` |
| 13 | continues accepted bulk work after the form unmounts | Edge | Unmount form while first upload is held, same session | Both uploads finish, one publication | Frontend unit | ✅ `src/components/__tests__/upload-modal/uploading.test.tsx::continues accepted bulk work after the form unmounts` |

| 14 | reports a failed final bulk publication | Error | Uploads finish but refresh rejects | Visible refresh failure; no unhandled rejection | Frontend unit | ✅ `src/components/__tests__/upload-modal/uploading.test.tsx::reports a failed final bulk publication` |

Existing observable regression names above will be assessed after the ownership cutover. This increment does not migrate mounted-library settings reads, URL capture, ZIP transfer, or tag drafts; those are separate lifetimes. No performance improvement is claimed from moving code.

Reproduction: the first two runs exposed the extra second-file POST but then left test-only TaskCenter state alive, contaminating later cases. Diagnosis found the component harness omitted the production bootstrap session listener. After installing that actual listener (without weakening cleanup), both transfer/job retirement cases failed on the extra `private-b.stl` POST; the same-session unmount control passed (2 failed / 1 passed, 4.13 seconds).

Functional checks: all 25 UploadModal uploading behaviors passed in 9.99 seconds. The first green attempt passed 24 and failed only the new error-toast expectation: production intentionally sanitizes an unknown Error to its established server-recovery message. The assertion now checks that actual public error contract; no error implementation was weakened. An integrated nine-file gate for BackupRunHistory, its owner/API, all three upload-dialog mirrors, native artifact upload, dependency boundaries and suite hygiene passed 214/214 in 38.42 seconds. Full integrated app/UI/domain typecheck passed. Full lint identified one concurrent M5 refresh ref-render violation; that owner's correction and the real bulk-upload headline remain pending.

Ordered M7 qualification after M6:245/245 upload, artifact transfer, Task Center,
events, shell and hygiene tests passed (8 files,10.27s). Types and lint passed.
Real pause/reload/resume18.3s and bulk21.9s passed; the old bulk failure was an
offscreen thumbnail arrangement, diagnosed with HTTP trace and screenshot,
corrected by revealing each card before requiring its lazy image. A real dropped
event/Job-read recovery case passed10.4s (45.1s including startup): the persisted
Job finishes while delivery is unavailable, then the existing Task Center shows
completion after reconnect without page reload. Earlier failing attempts remain
explicit; no performance claim or whole-M7 closure is made by this increment.
