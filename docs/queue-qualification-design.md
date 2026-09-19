# M01 queue qualification implementation notes

## Ownership

- Production import ownership remains unchanged in M01: Python coordinates and `BackgroundJob` remains the durable owner.
- The qualification harness is Rust. Each candidate library coordinates only disposable qualification jobs in its own upstream-owned schema.
- Candidate types stay inside candidate adapters. Contract scenarios consume a small qualification interface and sanitized evidence records.
- No production queue cutover, application migration, or competing consumer is introduced in M01.

## Exact candidates checked on 2026-09-17

- Apalis 0.7.4, crates.io checksum `504d52557a16b7b202941660f339c9182910d498cac40f93d15f6b31e6bef290`; `apalis-sql` 0.7.4 checksum `9d96124556e2190523c1a52acf3b3e02af564343b4fa888f0b712775ac80ee04`; tag `49f90e1304f8f218eb08ce6ca0f1b4934f3ed011`; MIT OR Apache-2.0 / MIT; SQLx `0.8.1`. Apalis 1.0.0-rc.10 is a prerelease and is recorded but is not an eligible stable replacement.
- Azums 1.0.1, crates.io checksum `c9a2d77fb435b959d88f4b694a010a8c9e3f932e5de8d5f8a2caa4b7056af41e`, tag `0f9671b38cff70494136734c3fe68f279b12fde7`, MIT OR Apache-2.0, MSRV 1.88, SQLx `0.8`. Disable default Redis and CLI features; enable only SQLite and PostgreSQL.
- Effectum remains excluded because it does not support PostgreSQL. Fang remains excluded unless its stable recovery contract changes.

Crates.io confirms those are the latest stable releases. At inspection time, `apalis` had 1,155,987 total downloads and `apalis-sql` 647,962; Azums had 238. Download counts are maintenance-risk context, not a correctness decision.

Recheck crates.io and upstream tags when the M01 branch is created. Record the immutable crate checksum and full resolved dependency graph.

A disposable combined compile probe resolves both candidates to one SQLx 0.8.6 graph and builds successfully with Tokio 1.53.1. The downloaded crate archives match all three registry checksums and disabling Azums defaults keeps Redis out of the graph. SQLx 0.9.0 exists but is outside both candidates' declared `0.8` compatibility range; using 0.8.6 is therefore the latest compatible line, with the exception recorded rather than forcing an unsupported upgrade.

The combined candidate graph passed an offline, lockfile-enforced all-targets check on M01's production Rust 1.91.0 toolchain. M01 CI and its original evidence remain tied to 1.91.0. M02 upgrades the production toolchain to 1.98.1 and reruns the unchanged qualification contracts before merge.

## Source findings that require executable reproduction

- Apalis 0.7.4 PostgreSQL batches acknowledgements with `WHERE apalis.jobs.id = Q.id`; it does not predicate on current worker, state, lease, attempt, or claim generation. Reproduce an old owner acknowledging after a successor claim.
- Apalis 0.7.4 SQLite acknowledgement checks `id` and `lock_by`, but not claim time/generation or lease validity. Reproduce expiry before reaping and worker-ID reuse.
- Apalis SQLite selects retryable `Failed` rows but the claim update requires `status = 'Pending'`. Prove whether failed jobs are stranded through the supported worker path.
- Apalis exposes pool-based enqueue; no stable public caller-transaction enqueue API was found. Demonstrate the orphan job that survives an application-acceptance rollback without writing a custom queue insert.
- Azums 1.0.1 completion checks job ID, running state, worker ID, and running attempt ID, but does not require `lock_expires_at > now()`. Reproduce completion after expiry before reaping, after a successor claim, and with worker-ID reuse.
- Azums SQLite selects candidates inside a write transaction and updates by ID without repeating eligibility. Stress concurrent claimers against a file-backed WAL database.
- Azums SQLite attempt startup begins a deferred transaction, reads the lease and prior attempts, then upgrades to an insert. A deterministic public-API fault boundary that holds and releases a concurrent writer makes that upgrade fail with `SQLITE_BUSY`; the ordinary quickstart path propagates the storage error. Record this separately from throughput because retrying the storage operation in application code would be additional integration machinery.
- Azums SQLite creates tables and discards additive `ALTER TABLE` errors while PostgreSQL uses SQLx migrations. Exercise clean install, supported prior-version upgrade, repeat startup, partial schema, backup, and restore.

Pre-branch probe: through Azums' public `StorageBackend`, real file-backed SQLite and disposable PostgreSQL jobs with a one-second lease were leased and given an attempt, then completed successfully 1.5 seconds later without reaping (`completion=Ok(())`, durable status `succeeded`). On both databases a second expired owner also renewed its already-expired lease (`late_heartbeat=true`). M01 must retain these as committed executable failing contracts; they are not yet the final candidate decision because the full matrix and supported-integration assessment still remain.

Azums' backend-specific `enqueue_in_tx` APIs do accept caller-owned SQLx transactions. The structured pre-branch harness verified both sides on file-backed SQLite and real PostgreSQL: an application row and queue row committed together with counts `(1, 1)`, while explicit rollback left counts `(0, 0)`. Record these as passing contracts 1 and 2; the expiry failures still disqualify 1.0.1 under the all-gates rule.

Azums also passes successor fencing after its reaper runs: on both databases the old attempt ID is refused, the successor remains `running` under its current owner, and this remains true when the successor reuses the same worker ID. Record these as passing contracts 7 and 8. The distinction matters: attempt fencing is implemented, but a worker that acts after its deadline and before reaping is still treated as current.

Pre-branch Apalis reproduction: an Apalis 0.7.4 PostgreSQL job was claimed by `old-worker`, returned to the queue through the public control API, and claimed by `new-worker` while the old handler remained blocked. The old poll interval was five seconds and the successor interval 20 ms, deterministically preventing the old poller from reclaiming the released row. The row was `("Running", Some("new-worker"))` immediately before the old handler returned. After the old worker's normal batched acknowledgement ran, the durable row became `("Done", Some("old-worker"))`. This reproduces the missing-current-owner predicate through public worker APIs on real PostgreSQL, rather than by executing a copied internal query. M01 committed the scenario as a regression test and captured the candidate version, database image digest, and sanitized output with its locked dependency graph on production Rust 1.91.0. M02 reruns the same contract on Rust 1.98.1.

A separate real-process recovery probe passes on PostgreSQL when every worker ID is process-unique. The harness initially reused `terminated-worker` across namespaces and exposed that Apalis worker IDs are global: the later worker registration changed the shared heartbeat row's `worker_type`, preventing the earlier namespace's orphan join from matching. That was a harness defect, not a candidate recovery failure. The committed probe uses UUID-suffixed worker IDs, kills the child process after the handler starts, and verifies a successor runs the same durable job. Production integration would need to make the same process-generation guarantee.

A matching file-backed SQLite reproduction used two sequential Apalis workers with the same stable worker ID. After the successor claimed the retried job, the durable row was `("Running", Some("shared-worker"))`; the delayed first attempt then changed it to `("Done", Some("shared-worker"))`. The SQLite acknowledgement's worker-ID predicate therefore does not fence attempts when an ID is reused. M01 must retain this as contract 8 evidence and give production workers process-unique execution generations regardless of the candidate decision.

The paired SQLite distinct-worker scenario passes: the old acknowledgement cannot change a successor owned by a different worker ID. Record that narrow pass alongside the reused-ID failure so the report attributes the gap to missing attempt/generation fencing rather than claiming the SQLite acknowledgement has no owner predicate.

The supported Apalis SQLite worker path also reproduced stranded transient failures. `failed-worker` returned one handler error, producing `("Failed", Some("failed-worker"), 1)`. A fresh `recovery-worker` polled for 1.5 seconds but never entered the handler, and the durable row remained unchanged. This matches the source mismatch between selecting retryable `Failed` rows and only claiming `Pending` rows. M01 must retain it as contract 13 evidence with a bounded timeout.

The Apalis pool-owned enqueue API also reproduced the atomicity gap on both databases. A supported enqueue committed one queue row; the caller's application transaction then rolled back its row, leaving counts `(0 application, 1 queue)`. No custom queue SQL was used. Retain this as failing contract 1 evidence and distinguish it from Azums' passing caller-transaction API.

## Crate and harness shape

- Add a non-publishable independent crate at `backend/qualification/queue` with its own lockfile. Rejected candidates must not enter the production extension workspace, lockfile, wheels, or container dependency graph. Pin candidate versions exactly and expose no candidate type outside `adapters/apalis.rs` or `adapters/azums.rs`.
- Use a Tokio current-thread runtime for deterministic contract scenarios and the normal multi-thread runtime only for contention/throughput scenarios.
- Use real file-backed SQLite with the application's WAL/busy-timeout/foreign-key policy and a real disposable PostgreSQL database.
- Give each alternative candidate its own disposable PostgreSQL database. Both upstream migrators use SQLx's default migration-history table, so running Apalis and Azums migrators in one database makes the second correctly reject the first candidate's unknown migration versions. Candidate isolation prevents a harness artifact; the report separately records the migration-ownership implication for any future application SQLx migrator.
- A thin CLI emits versioned JSON. Rust integration tests call the same scenario functions. A repository script provisions disposable databases and compares candidate evidence with the existing Python queue baseline.
- SQL inspection may assert durable state and set up fault boundaries. It must not implement claiming, acknowledgement, retry, heartbeat, or reaping on behalf of a candidate.
- Do not adapt around a failed contract by copying a candidate's internal SQL. A missing supported API is a qualification failure.

## Executable contract matrix

| # | Contract | Fault/action | Required observation |
| --- | --- | --- | --- |
| 1 | atomic acceptance rollback | application acceptance aborts after enqueue intent | no runnable queue row survives |
| 2 | atomic acceptance commit | application row and queue intent commit | both become visible together |
| 3 | claim once | 16 concurrent claimers | one current owner and one attempt |
| 4 | process-loss recovery | SIGKILL after handler starts | same job becomes runnable within bounded recovery time |
| 5 | reject completion after expiry | owner completes after lease expiry before reaper | completion is refused |
| 6 | reject renewal after expiry | owner heartbeats after lease expiry before reaper | renewal is refused and recovery remains possible |
| 7 | reject stale completion | successor reclaims, old owner completes | successor state and attempt remain intact |
| 8 | reject reused-worker stale completion | successor uses the same worker ID | old attempt/generation is still refused |
| 9 | reject stale publication | expired owner starts application publication | no Artifact row or storage commit is authorized |
| 10 | publish once after replay | crash after publication before queue acknowledgement | exactly one Artifact and one final queue outcome |
| 11 | preserve waiting attempts | prerequisite stays pending across repeated deferrals | failure attempt budget is unchanged |
| 12 | bound failures | handler repeatedly fails | configured terminal state at exact limit |
| 13 | retry failed work | one transient handler failure | job is reclaimed and completes |
| 14 | validate envelope | unknown version/type | no handler execution |
| 15 | preserve visible identity | supplied current PrintStash job ID | same public ID is returned through completion/recovery |
| 16 | priority ordering | mixed foreground/background jobs | foreground is preferred; aged background is admitted at 60 seconds |
| 17 | separate resource admission | claimable heavy jobs exceed permits | only admitted work starts; queue attempt semantics remain correct |
| 18 | graceful drain | shutdown during handler | recoverable checkpoint/queue state; no new claim after drain starts |
| 19 | maintenance drain | maintenance begins during polling | worker releases DB/storage use within bound |
| 20 | backup/restore | queued/running/retry rows and app data restored | identities and runnable intent survive; retired connections do not write |
| 21 | checkpoint resume | multi-stage job stops after durable checkpoint | successor resumes the next stage once |
| 22 | idempotent enqueue | same accepted command replayed | one executable intent |
| 23 | database parity | every contract on SQLite and PostgreSQL | equivalent outcomes and error categories |
| 24 | migration ownership | clean/repeat/prior/partial schema startup | deterministic controlled upgrade or explicit refusal |
| 25 | schema coexistence | queue schema in configured application DB | no collisions, hidden external service, or opportunistic worker migration |
| 26 | no broker dependency | network services absent except selected DB | qualification remains functional |

## Performance evidence

- Azums ships an `azums-perf` binary, but it uses shared in-memory SQLite and explicitly leaves CPU, RAM, allocation, disk and network counters null. It cannot supply the required file-backed cross-candidate comparison. Reuse its public batching/worker APIs and workload ideas where applicable; retain PrintStash's controlled container/resource collector for comparable evidence.
- The inspected Apalis crates do not ship a comparable benchmark binary. The common harness must drive its public storage/worker APIs rather than copied claim or acknowledgement SQL.
- Compare the current queue, Apalis, and Azums on SQLite and PostgreSQL under 2 CPU/2 GiB and 4 CPU/4 GiB.
- Keep the successful single-worker Azums SQLite timing separate from the contended result. Four-worker runs may sometimes finish, but a deterministic concurrent-writer probe proves that normal attempt startup can surface `SQLITE_BUSY`; do not hide that failure by unbounded benchmark retries.
- Repeated steady-state pairs exclude crash recovery from their elapsed time. Run recovery once per candidate/profile as a correctness and recovery-time diagnostic; the current queue's production 120-second lease and the candidates' configured qualification leases must remain visible rather than being averaged into unrelated throughput.
- Apalis steady-state measurements use a 30-second orphan threshold, while their isolated recovery scenario uses one second. A shorter steady threshold was observed to requeue eight of sixteen already-handled jobs while acknowledgement batching was still in flight; separating the thresholds prevents the harness from reporting lease-expiry artifacts as normal duplicate execution.
- Collect coordinator cgroup CPU and memory peaks outside the candidate process, and database byte growth outside the queue library. The candidate JSON records enqueue, enqueue-to-start, acknowledgement, enqueue-to-terminal, throughput, exact dependency versions/features, and completion/duplicate counts. The external collector records the resource profile and PostgreSQL service cost.
- Record idle polling CPU inside each process: the current coordinator uses Python process time and the Rust candidates use the stable `cpu-time` 1.0.0 crate rather than application-owned clock bindings. The controlled wrapper independently records total cgroup CPU and peak memory, while each fresh database records committed byte growth from its pre-migration size.
- One warm-up plus seven alternating measured pairs; expand to fourteen only when the noise rule requires it.
- Measure enqueue-to-start, claim, acknowledgement, steady throughput, contention throughput, crash recovery, idle CPU/wakeups/connections, database bytes per job, and maintenance-drain time.
- Preserve exact workload count, payload hash, success count, attempt count, and database version before accepting timing.
- Correctness failures disqualify adoption even if performance is better. Neutral performance is reported as neutral.

## Decision rule

- Prefer Apalis only if every gate passes on both databases through supported APIs.
- Select Azums only if Apalis fails and Azums passes every gate.
- If both fail, keep the current queue, report the exact failed contracts, and stop M03-M07. Continue the independent M02 and M08-M14 milestones through the existing execution path.
