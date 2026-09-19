# M01 queue qualification report

The executable M01 harness reproduces the decisive failures on real file-backed SQLite and real PostgreSQL. PR CI, controlled performance artifacts, and the exact-diff review remain milestone merge gates; they cannot turn a failed correctness contract into an adoption.

## Decision

Neither candidate evaluated in M01 qualifies. Keep the existing `BackgroundJob` queue and its Python execution owner. Defer M03–M07. M02 and M08–M14 remain independent and can proceed through the existing execution path.

The decision is final for the pinned stable releases. Performance cannot override a failed correctness contract; a future release must rerun the complete qualification procedure.

## Active migration scope

As of 2026-09-18, the Rust queue and persistence cutover is deferred from the
active OSS migration. M03–M07 are not prerequisites for M08–M14: those format,
compute, and acquisition stages continue through the existing Python
coordinator and `BackgroundJob` durable state. This avoids introducing an
unqualified queue while the independently reusable native modules are
completed.

This is a scope decision, not a claim that Rust has no database-backed queue
libraries. M01 proved that the pinned Apalis and Azums releases fail required
PrintStash contracts. It did not qualify every queue in the Rust ecosystem.
The production owner remains unchanged until a later milestone explicitly
reopens qualification and passes the same executable gates on real file-backed
SQLite and real PostgreSQL.

Candidates discovered after the original two-candidate procedure are recorded
for that future evaluation:

| Candidate | Inspected release | Initial fit | Required evidence before adoption |
| --- | --- | --- | --- |
| Worklane | 0.2.1 | SQLite and PostgreSQL brokers, receipt-based leases, priorities, and caller-transaction enqueue for PostgreSQL | Atomic caller-transaction enqueue on SQLite, stale-owner fencing, upgradeable pre-1.0 schemas, maintenance history, and the complete recovery/performance matrix |
| RustQueue | 0.3.0 | SQLite and PostgreSQL storage with an embedded worker and documented crash recovery | Caller-transaction enqueue, stale ACK fencing, schema upgrades, dependency footprint, and the complete recovery/performance matrix |
| pgqrs | 0.15.3 | PostgreSQL plus SQLite/Turso and durable workflow execution | Queue-only integration depth, ownership fencing, atomic acceptance, schema upgrades, and the complete recovery/performance matrix |
| Boson | Not yet inspected | Advertises pluggable SQLite and PostgreSQL backends | Stable published artifacts, immutable versions, maintenance evidence, and every correctness and performance gate |
| DBOS Python (official) | 3.0.0 | Durable queues and workflows on SQLite or PostgreSQL, caller-transaction enqueue, priorities, recovery, and concurrency/rate controls | Strongest newly discovered coordinator candidate; run the complete correctness/performance matrix and accept that the durable coordinator remains Python |
| DBOS Rust (official) | 0.5.0 | Mature durable-workflow model, queues, priorities, recovery, and concurrency controls, but its released Rust system database is PostgreSQL-only | Does not qualify for the OSS requirement that one embedded Rust queue use either the configured SQLite or PostgreSQL application database |
| `dbos-core` community port | 0.1.0 (yanked) | Its archived docs advertise SQLite and PostgreSQL behind one Rust workflow/queue API | Excluded: crates.io reports the only release as yanked and Cargo cannot resolve it |

Documentation reviewed for this inventory:
[Worklane PostgreSQL](https://docs.rs/crate/worklane-postgres/0.2.1),
[Worklane SQLite](https://docs.rs/worklane-sqlite/0.2.1/worklane_sqlite/),
[RustQueue](https://docs.rs/crate/rustqueue/0.3.0),
[pgqrs](https://docs.rs/crate/pgqrs/0.15.3),
[Boson](https://github.com/unified-field-dev/boson),
[official DBOS Python queues](https://docs.dbos.dev/python/reference/queues),
[DBOS Python database configuration](https://docs.dbos.dev/python/reference/configuration),
[DBOS Python transactional enqueue](https://docs.dbos.dev/python/reference/client#enqueue_in_transaction),
[official DBOS Rust v0.5.0](https://github.com/dbos-inc/dbos-transact-rust/tree/v0.5.0),
[archived `dbos-core` 0.1.0 docs](https://docs.rs/dbos-core/0.1.0/dbos/), and
[its crates.io release](https://crates.io/crates/dbos-core/0.1.0).

### DBOS assessment

DBOS was missing from the original M01 comparison and is recorded here so that
the queue decision does not imply it was evaluated. Its official SDKs have
different capabilities and must not be treated as one interchangeable library.
The releases below were inspected on 2026-09-19.

The official Python SDK 3.0.0 (MIT, Python 3.10+) supports durable queues on
both SQLite and PostgreSQL; SQLite is its default system database. The release
was published on 2026-09-16 and depends on SQLAlchemy 2.0.43+, psycopg 3.1+,
PyYAML 6.0.2+, python-dateutil 2.9+, websockets 14+, and Click 8.1+. Its
`enqueue_in_transaction` API accepts a caller-owned SQLAlchemy connection or
session and writes the workflow intent inside that transaction. When PrintStash
configures the DBOS system database as the application database, this
directly matches the atomic acceptance shape that Apalis failed. It also provides priorities, delayed work,
deduplication, partitioning, global/per-worker concurrency, rate limits,
recovery, and explicit queue listeners without Redis or a separate service.
This makes DBOS Python the strongest newly discovered candidate for a future
OSS queue qualification.

That fit does not establish production correctness. The transactional enqueue
cannot span a separate database, and the Python SDK still needs executable
evidence for stale completion/publication fencing, killed-process recovery,
SQLite contention, waiting without attempt consumption, schema upgrades,
shutdown, backup/restore, and PrintStash's foreground/background policy. It
also leaves Python as the durable coordinator: a DBOS workflow may call the
Rust engine through the narrow binding, but that architecture does not satisfy
a requirement that the coordinator itself run in Rust.

The official Rust SDK's latest stable release at inspection time is `dbos`
0.5.0, released on 2026-09-09 under MIT. It requires Rust 1.95 and SQLx 0.9
with only the `postgres` driver enabled. Its repository describes the system
database as PostgreSQL, contains only a PostgreSQL system-database
implementation, and runs its database CI against PostgreSQL and CockroachDB.
SQLite is therefore not a hidden or optional feature in that release.

The official SDK is technically relevant: it embeds the coordinator, durable
workflow state, recovery, queue priorities, per-worker/global concurrency, and
rate controls without Redis or a separate orchestrator. It still fails two
required PrintStash adoption gates before a benchmark would be meaningful:

- it cannot use PrintStash's default SQLite application database; and
- Rust transactional steps are not implemented, so application writes and
  workflow checkpoints cannot provide PrintStash's required atomic acceptance
  through the supported API. The project's own Rust widget-store documentation
  calls this write window at-least-once.

The Python SDK is the practical cross-database DBOS option; the official Go
SDK's SQLite support would add a second worker service and is not useful here.
A separate community crate named `dbos-core` published 0.1.0 with SQLite and
PostgreSQL APIs, but crates.io now marks that only release as yanked and Cargo
cannot resolve it. Archived docs are discovery evidence only; the crate is not
an installable production candidate. A future non-yanked release would still
need provenance, maintenance, and every executable gate before a queue cutover
could be reopened.

Reopening the queue work requires a new qualification PR. It must reuse the M01
harness and may extend its adapter boundary, but it must not add candidate code
to production until one release passes every contract. M15 cannot claim a
complete native execution cutover while the Python durable owner remains; the
achievable deferred-queue result is a native import processing pipeline driven
by the existing OSS queue.

## Ownership

| Concern | M01 owner |
| --- | --- |
| Production import coordinator | Python runtime, unchanged |
| Production durable state | `BackgroundJob` and application transaction, unchanged |
| Candidate compute | Rust qualification harness only |
| Candidate durable state | Disposable upstream-owned Apalis/Azums tables |
| Candidate migrations | Explicit harness setup before workers start |

M01 introduces no production consumer, application migration, queue cutover, or mixed ownership.

## Candidate versions

| Candidate | Exact stable version | Registry checksum | Database layer | License | Toolchain |
| --- | --- | --- | --- | --- | --- |
| Apalis | `apalis` 0.7.4 | `504d52557a16b7b202941660f339c9182910d498cac40f93d15f6b31e6bef290` | `apalis-sql` 0.7.4 (`9d96124556e2190523c1a52acf3b3e02af564343b4fa888f0b712775ac80ee04`) | MIT OR Apache-2.0 / MIT | Graph checks on Rust 1.91.0 |
| Azums | 1.0.1 | `c9a2d77fb435b959d88f4b694a010a8c9e3f932e5de8d5f8a2caa4b7056af41e` | SQLx 0.8 | MIT OR Apache-2.0 | MSRV 1.88; graph checks on Rust 1.91.0 |

Both candidates resolve to SQLx 0.8.6 and Tokio 1.53.1 in one locked graph. SQLx 0.9.0 is newer but outside both candidates' declared compatibility range. Azums defaults are disabled so Redis and its CLI are absent. Apalis 1.0.0-rc.10 is a prerelease and is not an eligible stable replacement.

## Decisive contract evidence

| Contract | Apalis SQLite | Apalis PostgreSQL | Azums SQLite | Azums PostgreSQL |
| --- | --- | --- | --- | --- |
| Reject completion after lease expiry | Pending committed evidence | Superseded by stronger stale-successor failure | **Fail:** completion succeeds 1.5 s into a 1 s lease | **Fail:** completion succeeds 1.5 s into a 1 s lease |
| Reject renewal after lease expiry | Pending committed evidence | Pending committed evidence | **Fail:** expired owner extends lease | **Fail:** expired owner extends lease |
| Reject stale successor completion | **Pass:** distinct worker ID preserves successor | **Fail:** old ACK changes successor's `Running/new-worker` row to `Done/old-worker` | **Pass:** old attempt ID refused after successor claim | **Pass:** old attempt ID refused after successor claim |
| Reject reused-worker stale completion | **Fail:** old attempt changes successor row to `Done` under reused worker ID | Pending committed evidence | **Pass:** attempt ID fences reused worker name | **Pass:** attempt ID fences reused worker name |
| Retry failed work from a fresh worker | **Fail:** `Failed/failed-worker/attempt=1` remains stranded | Pending committed evidence | Pending committed evidence | Pending committed evidence |
| Recover after worker process termination | Pending committed evidence | **Pass:** killed child process is reaped and a unique successor runs the job | Pending committed evidence | Pending committed evidence |
| Operate under database contention | Pending committed evidence | Pending committed evidence | **Fail:** attempt startup can propagate `SQLITE_BUSY` during a deferred read-to-write upgrade | Pending committed evidence |
| Atomic caller-owned acceptance transaction | **Fail:** application rollback leaves `(0 app, 1 queue)` | **Fail:** application rollback leaves `(0 app, 1 queue)` | **Pass:** commit `(1 app, 1 job)`; rollback `(0 app, 0 jobs)` | **Pass:** commit `(1 app, 1 job)`; rollback `(0 app, 0 jobs)` |

Any bold failure is sufficient to reject a candidate under the agreed all-gates rule. Pending rows are still implemented where they improve maintenance evidence; they cannot restore qualification after a decisive failure in the same released version.

## Reproduced observations

### Apalis PostgreSQL stale acknowledgement

The old worker uses a five-second poll interval and remains blocked inside its handler. The public `retry` control operation releases the job; a new worker polling every 20 ms claims it. The database is `("Running", Some("new-worker"))` before the old handler returns. Apalis's normal batched acknowledgement changes it to `("Done", Some("old-worker"))`.

The reproduction uses public worker and control APIs on real PostgreSQL with the locked dependency graph and Rust 1.91.0. It does not execute a copied internal acknowledgement query.

A later ordinary CI execution independently exposed the same ownership defect
through duplicate processing: the Apalis PostgreSQL verification expected 16
unique executions and observed 20, with four durable job IDs executed twice.
The workflow failed at the duplicate-execution assertion before producing a
performance result. This converts the earlier source-level concern into an
observed runtime qualification failure. The qualification workflow is now an
explicit `workflow_dispatch` diagnostic so rejected candidates cannot block
unrelated migration milestones; reopening the queue decision must run it
deliberately and treat any duplicate as a correctness failure.

### Apalis PostgreSQL process recovery

A child process claims the job and blocks inside the handler. The harness kills that process, then starts a successor with a new process-unique worker ID. The successor runs the same durable job within the bounded recovery interval, so this contract passes. Apalis worker IDs are global across queue namespaces; reusing a worker ID in an earlier probe changed the heartbeat row's namespace and produced a false failure. The final harness uses UUID-suffixed IDs and records the global-ID integration requirement.

The throughput path uses a 30-second orphan threshold and the isolated recovery path uses one second. A preliminary run used the recovery threshold for both and requeued eight of sixteen completed handler calls before Apalis flushed their acknowledgements. The final harness separates these concerns; duplicate counts now measure steady execution rather than an intentionally accelerated lease expiry.

### Apalis SQLite reused worker identity

Two sequential workers use `shared-worker`. After retry and successor claim, the row is `("Running", Some("shared-worker"))`. The first attempt's delayed acknowledgement changes it to `("Done", Some("shared-worker"))`, showing that worker identity alone does not fence execution attempts.

### Apalis SQLite transient retry

One handler failure creates `("Failed", Some("failed-worker"), 1)`. A fresh `recovery-worker` polls for a bounded 1.5 seconds but never enters its handler, and the row remains unchanged. This matches the source mismatch between selecting retryable `Failed` rows and only updating `Pending` rows during claim.

### Apalis atomic acceptance

Apalis exposes pool-owned enqueue rather than a supported caller-transaction enqueue API. On both databases, the supported enqueue committed one queue row and the caller's application transaction then rolled back its application row, leaving `(0 application, 1 queue)`. The harness uses no copied queue SQL. This fails atomic import acceptance.

### Azums expired completion and renewal

On real file-backed SQLite and real PostgreSQL, a job receives a one-second lease and execution attempt. After 1.5 seconds without reaping, `mark_succeeded` returns success and the durable state becomes `succeeded`. A second expired job returns `true` from `extend_lease`. The backend checks state, worker and attempt identity, but not that the current lease is still live.

### Azums atomic acceptance

Azums exposes backend-specific `enqueue_in_tx` methods for caller-owned SQLx transactions. On both databases, the qualification harness committed one application row and one queue row together, then repeated the operation and rolled it back, leaving neither row. These contracts pass. They do not compensate for accepting expired owners because qualification requires every durability gate.

### Azums successor fencing

After a one-second lease expires and the supported reaper returns the job to the queue, a successor claim receives a new attempt ID. On both databases, completion with the old attempt is rejected and the successor remains `running`, including when it reuses the same worker name. This passes stale-successor fencing. The failing case is the interval after the lease deadline and before reaping, when the expired owner can still complete or renew.

### Azums SQLite contention

A single-worker, file-backed WAL workload completes. With concurrent workers, attempt startup intermittently returns SQLite code 5 (`database is locked`). The minimized reproduction is deterministic: it leases a job through the public API, holds an independent writer, starts the public `start_attempts_batch` operation, then commits the writer. Azums begins a deferred transaction, reads the current lease and attempt number, and only then upgrades to an insert; the changed WAL snapshot makes that upgrade fail. The standard quickstart path propagates storage errors, while the upstream chaos test survives by catching and retrying them. PrintStash would therefore need candidate-specific storage retry logic for contended SQLite operation, which violates the supported-integration gate and is included in the rejection evidence rather than hidden by benchmark retries.

## Maintenance assessment

- Apalis has the longer release and adoption history. The stable release nevertheless fails durable ownership contracts required by Artifact publication.
- Azums is very new: the inspected tag has no GitHub release object or signature, and the crate had 238 downloads at inspection time. Its smaller history is contextual risk; the expiry failures are the rejection reason.
- Apalis and Azums each use SQLx's default migration-history table. Alternatives therefore run in separate disposable databases during qualification. Any future adopted queue would need one controlled migration owner coordinated with PrintStash's own SQLx persistence migrations.
- Effectum remains excluded because it lacks PostgreSQL support.
- Fang remains excluded unless a stable release removes the documented interrupted-job recovery limitation.
- A fork, copied SQL acknowledgement layer, or custom queue framework is outside M01 and is not an implicit fallback.
- `cargo-audit` reports RUSTSEC-2023-0071 in `rsa` 0.9.10 through the lockfile-only `sqlx-mysql` package. MySQL is disabled and absent from the compiled graph, no fixed `rsa` release exists, and CI names this one inactive advisory explicitly while continuing to fail every other advisory.

The opt-in `Queue qualification benchmark` workflow builds the current queue and both pinned candidates before measurement, then runs fresh-database 2 CPU/2 GiB and 4 CPU/4 GiB profiles serially. It rotates implementation order for one warm-up and seven pairs, expands once to fourteen when coefficient-of-variation thresholds require it, and performs no failed-operation retries. Evidence includes process idle CPU, total cgroup CPU, peak container memory, committed database growth, timing distributions, exact image IDs, and one separate recovery diagnostic per implementation.

## Required committed evidence

- [x] Candidate crates are isolated behind qualification adapters and a test-only lockfile; no candidate type or dependency enters production modules, wheels, or images.
- [x] Real file-backed SQLite and real PostgreSQL reproduce every decisive observation.
- [x] Atomicity, recovery, contention, shutdown, backup/restore, migration ownership, and broker-absence rows have explicit outcomes.
- [x] Current queue, Apalis and Azums have controlled 2 CPU/2 GiB and 4 CPU/4 GiB results on both databases.
- [x] Raw evidence contains versions, image digest, settings, counts and hashes without URLs or credentials.
- [x] Rust format, Clippy, tests and native coverage pass on production Rust 1.91.0.
- [x] Backend fast and coverage lanes, exact-diff security review, and current-revision CI pass.

## Controlled performance results

Run [`35276786587`](https://github.com/xiao-villamor/PrintStash/actions/runs/35276786587) measured merge ref `1a74b2e5fc20a4d6e16ef24c538a6f38d477a7ef`. Its tree matches M01 implementation commit `93840af9a4a1a83736950e760174fbd6ec317d18`. All four profiles preserved accepted/completed counts, produced no duplicate executions, retained SHA-256 release image identities, and passed the sanitized-artifact scan. All four profiles crossed the declared variability threshold and used the single allowed extension from seven to fourteen measured pairs.

| Profile | Pairs | Noisy | Current jobs/s | Apalis jobs/s | Azums jobs/s | Current start p95 ms | Apalis start p95 ms | Azums start p95 ms |
| --- | ---: | :---: | ---: | ---: | ---: | ---: | ---: | ---: |
| sqlite-2cpu | 14 | yes | 150.52 | 106.31 | 1218.04 | 588.41 | 1142.29 | 81.97 |
| sqlite-4cpu | 14 | yes | 247.17 | 142.78 | 2011.08 | 368.06 | 851.48 | 52.28 |
| postgres-2cpu | 14 | yes | 95.83 | 315.09 | 174.85 | 891.44 | 328.23 | 442.12 |
| postgres-4cpu | 14 | yes | 104.09 | 343.47 | 377.84 | 832.23 | 325.38 | 170.13 |

These timing results do not change the decision. Apalis and Azums each fail required durability contracts, while M01 leaves the production queue unchanged. Full enqueue, acknowledgement, idle CPU, total CPU, peak memory, database growth, recovery, version, image and corpus evidence is retained in the four workflow artifacts.

Artifact digests: `sqlite-2cpu` `sha256:8498aa29de6bcabd46038537bd033c283548febab63b1260c5c88431a7a98fc5`, `sqlite-4cpu` `sha256:6cabb751d4695796ec1ba6f69900ede60026391b9c966938bffa61f8268444d0`, `postgres-2cpu` `sha256:84e9b053d100b57fcfe52aa776564079714a4670ea6bdaa7d288ee9e060ef7f0`, `postgres-4cpu` `sha256:4c67283639b1038e1b052d292864cf3e9a48fe4423e7806b2c17cb25d1ec78bb`.

## Test coverage matrix

| # | Behaviour | Category | Observable outcome | Tier | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | Apalis acceptance rollback on both databases | Error | Application rollback leaves a queue orphan, proving the supported API cannot join acceptance | Integration | ✅ |
| 2 | Apalis stale PostgreSQL acknowledgement | Error | Delayed old acknowledgement overwrites the successor claim | Integration | ✅ |
| 3 | Apalis reused-worker SQLite acknowledgement | Error | Delayed old attempt completes a successor using the same worker ID | Integration | ✅ |
| 4 | Apalis distinct-worker SQLite acknowledgement | Edge | Delayed old attempt cannot complete a differently named successor | Integration | ✅ |
| 5 | Apalis transient SQLite failure | Error | Fresh worker cannot reclaim the retryable `Failed` row | Integration | ✅ |
| 6 | Apalis PostgreSQL process termination | Error | Killed child worker's durable job runs under a process-unique successor | Integration | ✅ |
| 7 | Azums acceptance commit and rollback on both databases | Happy/Error | Application and queue rows commit or roll back atomically | Integration | ✅ |
| 8 | Azums expired completion and heartbeat on both databases | Error | Both expired owner operations are incorrectly accepted and recorded as candidate failures | Integration | ✅ |
| 9 | Azums successor attempt fencing on both databases | Edge | Old attempt is refused for distinct and reused worker names | Integration | ✅ |
| 10 | Azums SQLite deferred-transaction contention | Error | Public attempt start returns bounded `SQLITE_BUSY`; no retry loop hides it | Integration | ✅ |
| 11 | Candidate single-worker SQLite measurement | Performance | Counts, duplicates, latencies, throughput, idle interval, and recovery are recorded | Integration | ✅ |
| 12 | Candidate PostgreSQL measurement | Performance | Both public adapters complete verified work on isolated real databases | Integration | ✅ |
| 13 | Controlled 2/2 and 4/4 comparison | Performance | One warm-up and seven or fourteen paired runs with resource and DB-growth counters | CI benchmark | ✅ run `35276786587` |
| 14 | Locked dependency advisory and license scan | Security | Active graph passes pinned cargo-audit and cargo-deny tools; the one inactive lockfile advisory is named and reviewed | CI | ✅ required current-revision checks |
| 15 | Production queue remains unchanged | Regression | Existing backend, frontend, packaging, and import gates pass | CI/E2E | ✅ required current-revision checks |

## Rollback

M01 changes no runtime behavior. Removing the qualification crate, scripts and documentation returns to the M00 state; the production queue and schema are untouched.
