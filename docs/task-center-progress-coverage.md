# Grouped upload progress coverage

The grouped upload must retain one task while its server jobs are pending or
running. Its progress represents the complete expected upload, including jobs
that have not yet been submitted. Previously these intermediate states were
executed incidentally by upload UI tests; their assertions only checked the
request or final completion.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| T001 | reports grouped progress across unfinished jobs | Happy | Completed mesh, G-code at 40%, three expected jobs | One running task, 47%, current G-code detail | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::reports grouped progress across unfinished jobs` |
| T002 | keeps unknown grouped progress pending | Edge | Linked pending job without progress | One pending task, zero progress, current filename | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::keeps unknown grouped progress pending` |
| T003 | reports a failed grouped upload | Error | One linked server job fails | One failed task, complete progress, literal failure detail | Frontend unit | ✅ `frontend/src/lib/__tests__/task-center.test.ts::reports a failed grouped upload` |

Validation: app 2,171 tests, domain 60, UI 198 passed. The app library branch coverage rose to 85.41%, so its two-sided floor is raised from 84.9% to 85.2%. No runtime code changes.
