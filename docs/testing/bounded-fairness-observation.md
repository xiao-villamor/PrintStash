# Bounded fairness observation

A Deep CI coverage run completed both backfills but reached the fixture's wall
limit while two of 81 foreground uploads remained pending. Its two assertions
share that fixture, so this is one failed experiment with two setup errors.
The original failure remains preserved, rather than retried or waived.

The observer reloaded every accepted upload, its mesh Job and derivatives every
100ms, including completed history. Its SQL/ORM work grew with cumulative
throughput and contributed load to the process whose scheduler it measures.
This is an identified source of avoidable work; the log alone does not attribute
the entire deadline overrun to it. Coverage on GitHub must validate the final
result.

Keep accepted Job IDs for exact cumulative accounting, but read only pending
inputs and the two backfill Artifacts. Completion removes an input only after
both derivative outputs are READY. Duplicate acceptance and unknown completion
raise before changing progress. The real E2E asserts the maximum observed input
rows equals its six-item window. Native budgets, active-backfill checks, arrival
cadence and cap, 110-second deadline and 130-second parent limit remain intact;
every accepted foreground upload still has to finish. Production code does not
change.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | Completed uploads leave the observation window | Happy | 1,000 completed uploads | Pending read window returns to empty; cumulative ready/submitted remain exact | Repo | ✅ `tests/repo/test_ingestion_fairness_progress.py::TestForegroundProgress::test_completed_uploads_leave_the_observation_window` |
| 2 | Incomplete uploads stay pending | Edge | Two accepted uploads; one completes | Only remaining upload stays in the read window; ready count is one | Repo | ✅ `tests/repo/test_ingestion_fairness_progress.py::TestForegroundProgress::test_incomplete_uploads_stay_pending` |
| 3 | Refuses duplicate uploads | Error | An already completed Job ID is accepted again | Raises without changing totals | Repo | ✅ `tests/repo/test_ingestion_fairness_progress.py::TestForegroundProgress::test_refuses_duplicate_uploads` |
| 4 | Refuses unaccepted completion | Error | Completion set mixes pending and unknown Job IDs | Raises without partially completing accepted work | Repo | ✅ `tests/repo/test_ingestion_fairness_progress.py::TestForegroundProgress::test_refuses_unaccepted_completion` |
| 5 | Backfill progresses during sustained interactive arrivals | Happy | Real DBOS/native workers; six-item continuous arrival window | Both backfills progress during contention; every upload finishes; SQL observation window stays at six inputs | E2E | ✅ `tests/e2e/test_ingestion_fairness.py::TestIngestionFairness::test_backfill_progresses_during_sustained_interactive_arrivals` |
| 6 | Full capacity backfill eventually completes | Edge | Queued backfills each need full native capacity | Both admitted/completed before the unchanged 110-second bound while foreground remains pending | E2E | ✅ `tests/e2e/test_ingestion_fairness.py::TestIngestionFairness::test_full_capacity_backfill_eventually_completes` |

Historical remote failure: **18,936 passed, 2 fixture errors in 3151.06s**. Backfills completed at30.76/97.37s; two foreground inputs were still pending at the wall bound. Compatibility ordinary18,938PASS and resources486PASS are separate preserved successful jobs. They do not turn the failed Backend run into PASS. Initial focused selection: **6 passed in43.39s**. Final validation follows the preserved check of all observed mesh Jobs. No local full, coverage or Deep CI ran.

Final focused experiment: **6 passed in62.29s**, including both real E2E assertions with the unchanged deadline and four progress cases. The harness regressions live in the repo tier because they defend test infrastructure, not production code; their final repo selection and path invariant are recorded separately. Selections overlap and are not summed. Ruff, formatting and whitespace checks pass.

Repo progress/path validation: **673 passed in19.59s**, including four progress assertions; these overlap the initial focused selection. No hygiene exception or test-tier rule was changed.
