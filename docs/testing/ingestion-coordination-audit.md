# Batch ingestion audit: where the time, CPU and memory go

13 October 2026. Branch `feat/transparent-gpu-ingestion`, PR #430. An earlier
audit of this branch measured a harness artefact; see "Why the earlier audit
could not move".

## Result

200 models (100 STL, 94 OBJ, 6 STEP; 604 MiB; selection
`a76d027b1984…`), fed through the web UI's upload protocol into a fresh
container with real DBOS and SQLite, until every file has terminal metadata and
thumbnail derivatives. Two interleaved rounds; the host was faster in round two.

| Image | Round | All done | Models visible | CPU | Peak memory | Thumbnail after commit, median / p95 | Refused (429) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `main` (03b89162) | 1 | 760 s | 405 s | 1,436 s | 1,891 MiB | 172 s / 365 s | 104 |
| `main` | 2 | 643 s | 430 s | 1,180 s | 1,762 MiB | 168 s / 241 s | 91 |
| Branch before this work (deeb23e4) | 1 | 505 s | 502 s | 1,093 s | 1,405 MiB | 4.0 s / 19.1 s | 0 |
| Branch before this work | 2 | 393 s | 391 s | 595 s | 2,281 MiB | 1.8 s / 8.7 s | 175 |
| **Now** | 1 | **375 s** | 373 s | **876 s** | 1,369 MiB | 3.3 s / 21.3 s | 0 |
| **Now** | 2 | **198 s** | 196 s | **483 s** | 1,729 MiB | 1.9 s / 10.6 s | 0 |

- Against the branch before this work: 26% and 50% less time, 20% and 19% less CPU.
- Against `main`: 51% and 69% less time, 39% and 59% less CPU. On `main` the
  models are visible after about seven minutes but thumbnails trail by minutes,
  because it renders on CPU.
- Outputs are byte-identical between the branch before and now: 200 metadata
  rows, 192 thumbnails, and the same 8 invalid sources fail the same way.
  `main` produces the same metadata and failures; its thumbnails differ because
  its renderer differs.
- "Refused" counts uploads the server answered with `429 rate_limited`. The
  harness retried them after one second; the web UI of those versions had no
  retry and showed each as a failed file.

Idle, a freshly started container used 0.238 of a core before and 0.125 now.

Run-to-run variance on this WSL2 host is large: the same image finished 60
models in 96 s and 123 s, and whole rounds differ by up to 2×. Comparisons here
are within a round, and each claim holds in both rounds.

## What was wrong

**The server limited upload sessions to 30 per minute.** The web UI uploads a
batch one file at a time, each as its own session, so a 200-file drop could not
go faster than one file every two seconds, and files that arrived faster were
refused. In round two the branch before this work was held at exactly that rate
(200 files in 391 s, 175 refusals); with the limit raised the same server
finished in 196 s.

**Coordination, not geometry, is the per-file cost.** In a 100 s burst of 60
models the API process issued about 1,135 SQL statements and 65 write
transactions per model and used about 2.8 CPU-seconds per model. All geometry
reading and measuring is about 0.4 CPU-seconds per model in other processes;
GPU render and pixel readback are under 0.05.

**SQLite's single writer was busy 78% of the time.** Threads waited 407–490
thread-seconds for a write lock that was held 82–88 s. A Job progress write
takes 1.8 ms alone and 10–16 ms in the running system, because every statement
round trip releases the GIL and queues for it again behind 30–40 other threads.
Feeding four uploads at once therefore finished no sooner than one at a time.

**Connections were being rebuilt.** SQLAlchemy's default pool keeps five
connections and discards the rest on return. A new SQLite connection re-parses
the schema (773 objects, 186 KB of SQL) before its first statement: 5.3 ms
against 0.011 ms for a kept one. One 60-model run opened 718 connections.

**An idle server polled twelve job queues four times a second**, each look
costing three or four statements: 93% of idle CPU.

**DBOS left its SQLite file in rollback-journal mode with full
synchronisation**, and begins every transaction `IMMEDIATE`.

**Machine bookkeeping was audited row by row.** 200 uploads wrote 9,462 audit
rows; 5,354 of them recorded Job, reconciler-cursor and capacity-claim
heartbeats.

## What changed

| Change | Where | Measured |
| --- | --- | --- |
| Upload session limit 30 → 600 per minute (finalize 60 → 600, plan 180 → 1,200) | `api/v1/artifact_uploads.py` | Refusals 91–175 → 0; removes the two-seconds-per-file floor |
| A refused upload waits and retries instead of failing | `frontend/src/lib/artifact-upload.ts` | Covers `rate_limited` and `staging_capacity_exceeded`, up to ten minutes |
| SQLite pool keeps 24 connections (`VAULT_SQLITE_POOL_SIZE`) | `db/session.py` | Connections opened per 60-model run 718 → about 30 |
| Background lanes polled every 2 s (`VAULT_JOBS_RELAXED_QUEUE_POLL_INTERVAL_SECONDS`) | `work/contracts.py`, `work/catalog.py`, `runtime/engine/dbos_engine.py` | Idle CPU 0.238 → 0.125 cores |
| Job engine's SQLite file opened in WAL with the configured synchronisation | `runtime/engine/dbos_engine.py` | Contract suite against real DBOS passes |
| No row audit for Job, reconciler cursor, fence, executor and capacity bookkeeping | `db/models/work.py`, `db/models/capacity.py` | Audit inserts per model 47 → 17 |
| A step checks its ownership with a read, not the write lock | `work/runner.py`, `work/jobs.py` | One write transaction fewer per step |
| Commit stages reported only once a commit has run one second | `ingestion/ingestion.py` | Two write transactions fewer per small file |
| Cancellation probed every 0.5 s (was 0.2 s); worker memory sampled every 0.1 s (was 0.025 s) | `core/cancellation.py`, `media/mesh_isolation.py` | Fewer probes and `/proc` walks per worker |

The server-side changes together, on traced 60-model bursts: statements per
model 1,135 → 849–922; write transactions per model 64.6 → 55.5–59.2;
write-lock time per model 1,360 ms → 1,056–1,253 ms.

Tried and not kept:

- **Uploading three files in parallel from the web UI.** 389 s and 204 s
  against 375 s and 198 s sequentially, with per-file latency three times
  higher. The server is coordination-bound, so parallel feed adds waiting.
- **An in-process queue for SQLite writers.** It made waits fair (longest 4.8 s
  → 0.5 s) but did not reduce their total or the run time: the lock is
  genuinely held, not lost to polling.
- **A shorter interpreter switch interval.** No effect at 0.5 ms; worse at 0.1 ms.

## Memory under a limit

The same 200 models, web UI protocol, product default settings (one render
slot), container memory limit as shown.

| Image | Limit | All done | Models visible | Peak memory | OOM kills | Outputs |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `main` | 2 GiB | 769 s | 404 s | 1,490 MiB | 0 | 192 thumbnails, 8 expected failures |
| Now, GPU auto | 2 GiB | 410 s | 146 s | 1,591 MiB | 0 | 192 thumbnails, 8 expected failures |

Neither image reaches a 2 GiB limit on this corpus (largest source 60.5 MiB).
With default settings the thumbnails trail the uploads because one render slot
serves them; the models themselves are visible in 146 s.

The worker memory budget is half of the container limit
(`VAULT_MESH_MEMORY_BUDGET_FRACTION`) and does not account for what the API
process (about 360 MiB high-water) and, with GPU enabled, the compute broker
(about 290 MiB) already hold.

## Why the earlier audit could not move

Its harness kept at most four uploads in flight and looked for finished ones
once per second, so it fed about one model per second whatever the server did;
the 234 s it reported for 200 models was that feed rate. It used
`POST /ingest/model`, which the web UI does not use for uploads and which is not
subject to the session limit. It compared single runs whose differences (±3%)
are far inside this host's variance, and never ran `main` or a memory limit.

## What is left

The server still spends about 2.5 CPU-seconds of single-threaded coordination
per model. The largest remaining holders of the write lock, per 60 models: Job
bookkeeping about 12 s, search projection 10.7 s, capacity claims 8.1 s,
storage publication locators 6.9 s, derivative publication 6.7 s. Reducing them
means restructuring those transactions and is planned as separate changes.
Moving parsing or authoritative measurement to GPU would address about 11% of
the CPU and is not planned.

## Method

- Harness: `bench2.py` (kept outside the repository with the corpus cache).
  `--feed ui` drives one file at a time through `POST /artifact-uploads`, the
  plan, chunk `PUT`s and `finalize`, then waits for the import Job, as
  `frontend/src/lib/artifact-upload.ts` does. `--feed burst` keeps four
  `POST /ingest/model` uploads in flight. The clock stops when every accepted
  file has terminal metadata and thumbnail rows.
- Each run is a fresh container and database from an immutable image; cached
  originals, no network. GPU through Mesa D3D12 in WSL2, `VAULT_COMPUTE_MODE=auto`,
  four native slots.
- CPU and memory come from the container's cgroup; CPU by role from `/proc`
  sampled every 250 ms.
- SQL counts, lock waits and lock holds come from an opt-in tracer loaded only
  in traced runs; timed comparisons use untraced runs.
- The harness polls each Job every 0.2 s where the browser listens on a socket,
  which adds a few statements per file equally to every image.

## Coverage

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | rate limits repeated session creation | Error | one more create than the per-minute limit | last response `429 rate_limited` | Integration | ✅ `integration/api/v1/test_artifact_uploads.py::TestArtifactUploads::test_rate_limits_repeated_session_creation` |
| 2 | waits out a rate-limited session request | Happy | create refused once with 429 | upload completes with its Job; one 500 ms wait | Frontend unit | ✅ `src/lib/__tests__/artifact-upload.test.ts` |
| 3 | waits for staging room without opening a second session | Edge | finalize refused twice with 507 | one create, one chunk; waits 500 ms then 1 s | Frontend unit | ✅ same file |
| 4 | reports any other refusal without waiting | Error | create refused with 400 | rejects with that code; no wait | Frontend unit | ✅ same file |
| 5 | stops waiting once the server has stayed full for ten minutes | Boundary | create always refused with 507 | rejects after 10 min of waits | Frontend unit | ✅ same file |
| 6 | stops waiting when the upload is paused | Edge | abort during the wait | rejects with the pause; one create | Frontend unit | ✅ same file |
| 7 | sqlite keeps the configured number of connections | Happy | `sqlite_pool_size=7` | pool options `{7, 16}` | Integration | ✅ `integration/db/test_session.py::TestPoolOptions` |
| 8 | postgres keeps the library pool | Edge | PostgreSQL URL | no pool override | Integration | ✅ same class |
| 9 | a burst above the library default reuses its connections | Happy | 12 connections taken and returned three times | 12 connections opened in total | Integration | ✅ same class |
| 10 | opens the file in write-ahead mode | Happy | new engine SQLite file | `journal_mode` = `wal` | Integration | ✅ `integration/runtime/engine/test_dbos_engine.py::TestSqliteSystemConnection` |
| 11 | follows the configured synchronisation | Edge | `NORMAL`, `FULL` | `synchronous` = 1, 2 | Integration | ✅ same class |
| 12 | waits out a locked file | Edge | new connection | `busy_timeout` = 30,000 ms | Integration | ✅ same class |
| 13 | converts a file left in rollback-journal mode | Edge | existing file with a row | `wal`; row still readable | Integration | ✅ same class |
| 14 | is usable from another thread | Edge | connection used off its creating thread | query returns | Integration | ✅ same class |
| 15 | refuses a database without a file | Error | `sqlite://` | `ValueError` | Integration | ✅ same class |
| 16 | a sqlite engine hands dbos that connection | Happy | engine on a SQLite URL | config's creator yields a `wal` connection | Integration | ✅ same class |
| 17 | a postgres engine keeps the library connection | Edge | engine with a schema | no engine kwargs | Integration | ✅ same class |
| 18 | the real engine runs on that file | Happy | port contract suite, DBOS on SQLite | 78 contract tests pass | Contract | ✅ `contract/runtime/engine`, `contract/modules/work` |
| 19 | a lane is prompt unless declared otherwise | Happy | default `Lane` | `pace` is `PROMPT` | Unit | ✅ `unit/modules/work/test_contracts.py::TestLanePace` |
| 20 | pace requires a closed enum | Error | `pace="relaxed"` | `TypeError` | Unit | ✅ same class |
| 21 | lanes someone waits on stay prompt | Happy | ingest, derive (native, light), network, printing, reconcile | `PROMPT` | Unit | ✅ same class |
| 22 | background upkeep lanes are relaxed | Happy | search, maintenance, notify, similarity, captions, expansion | `RELAXED` | Unit | ✅ same class |
| 23 | a relaxed lane is polled at the relaxed cadence | Happy | base 0.25 s, relaxed 2 s | queue registered at 2 s | Unit | ✅ `unit/runtime/engine/test_dbos_engine.py::TestQueues` |
| 24 | a relaxed lane is never polled faster than a prompt one | Boundary | base 3 s, relaxed 2 s | queue registered at 3 s | Unit | ✅ same class |
| 25 | a job write leaves no row trail | Happy | Job updated | no `jobs` audit rows | Integration | ✅ `integration/modules/administration/test_audit.py::TestBookkeepingRows` |
| 26 | a reconciler cursor write leaves no row trail | Happy | cursor inserted | no `reconcile_cursors` audit rows | Integration | ✅ same class |
| 27 | a capacity claim leaves no row trail | Happy | claim created and deleted | no `capacity_reservations` audit rows | Integration | ✅ same class |
| 28 | a library write is still recorded | Edge | Model renamed | `models` audit row with `update` | Integration | ✅ same class |
| 29 | the current attempt owns its job | Happy | running Job, matching epoch and attempt | `True` | Integration | ✅ `integration/modules/work/test_jobs.py::TestOwnsExecution` |
| 30 | a stale claim does not own the job | Error | replaced epoch; superseded attempt; other state | `False` | Integration | ✅ same class |
| 31 | a missing job is owned by nobody | Edge | unknown id | `False` | Integration | ✅ same class |
| 32 | the check does not become the writer | Edge | after the check | connection not in a transaction | Integration | ✅ same class |
| 33 | a step of a withdrawn attempt does not run | Error | cancelled or superseded Job | step skipped | Integration | ✅ existing `integration/modules/work/test_runner.py` |
| 34 | a commit finished at once reports no stage | Happy | stages within 0.4 s | no Job update | Unit | ✅ `unit/modules/ingestion/test_ingestion.py::TestPacedReport` |
| 35 | a stage entered after the threshold is reported | Boundary | stage at exactly 1 s | one update with its fields | Unit | ✅ same class |
| 36 | every later stage of a long commit is reported | Happy | stages at 5 s | both reported | Unit | ✅ same class |
| 37 | each commit measures from its own start | Edge | second commit 30 s after the first | its first stage is not reported | Unit | ✅ same class |
| 38 | throttles durable checks | Boundary | checkpoints at 0, 0.4, 0.5 s | probes at 0 and 0.5 s | Unit | ✅ `unit/core/test_cancellation.py::TestCancellationScope` |
| 39 | a forced checkpoint probes inside the throttle | Edge | forced checkpoint at the same instant | second probe | Unit | ✅ same class |
| 40 | a worker over its memory budget is still stopped | Error | worker exceeding the budget with 0.1 s sampling | `RESOURCE_LIMIT` | Integration | ✅ existing `integration/modules/media/test_mesh_isolation.py`, `unit/modules/media/test_mesh_isolation.py` (401 supervision and isolation tests pass with the new interval) |
| 41 | a folder upload through the browser completes without failed files | Happy | 200 files through the UI protocol | 200 Jobs completed, 0 refusals | E2E | ⏭️ N/A — measured by the benchmark above; a Playwright case would need a 30-plus file fixture to cross the old limit |
