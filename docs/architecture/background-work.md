# Background work

Everything PrintStash does outside a request (imports, derivatives, backups,
scans, notifications, fleet dispatch, audits, migrations, retention) is a
**Job** of a registered **Job Definition**, executed by a durable engine behind
a port. Why it is shaped this way is in
[ADR 0008](../adr/0008-job-engine.md); this page is how it works.

```text
 request / watcher / printer event          tick (every JOBS_RECONCILE_INTERVAL)
            │ commit intent, then nudge(definition)      │
            ▼                                            ▼
   ┌────────────────────── reconciler pass (one per source) ─────────────────┐
   │ repair: Job rows ⨯ engine evidence → decide() → resubmit/interrupt/fail  │
   │ discover: source.pending(now, headroom) → create Job → submit            │
   └─────────────────────────────┬────────────────────────────────────────────┘
                                 ▼
                 JobEngine port (DBOS, or inline in tests)
                   lanes = queues · execution_id · dedupe_key
                                 ▼
                    runner: fences → steps → settle Job → nudge
```

## The model

| Term | Code | Meaning |
| --- | --- | --- |
| Job | `db.models.Job`, `work.jobs` | The user-visible record of work on one subject, with its status, owner and attempts. The row is also the pending marker the reconciler finds. |
| Job Definition | `work.contracts.JobDefinition` | Its name is a `JobKind` member (`<owner>.<verb>`), plus a lane, ordered steps, a label, an optional source, and what cancel/retry/failure do to the subject. Declared by the owning module in `<module>/jobs.py`. |
| Step | `work.contracts.Step` | An idempotent unit with its own `RetryPolicy`. |
| Subject | `Job.subject_key` | The domain key the Job's intent belongs to (`file/42`, `library/3`). One active Job per definition and subject (`uq_jobs_active_subject`). |
| Work Source | `work.sources` | Computes pending subjects from domain state, bounded by the room it is given. `StateSource` for domain rows, `ScheduleSource` for a cadence. |
| Lane | `LaneName`, `work.catalog` | A concurrency class and an engine queue: `ingest`, `derive.native`, `derive.light`, `similarity`, `network`, `notify` (partitioned per channel), `printing` (per printer), `maintenance`, `search`, `captions`, `expansion`, `reconcile`. |
| Priority | `WorkPriority` | `interactive` (a user is waiting) or `backfill`. A child Job never raises it. |
| Fence | `work.fences` | A database lease (holder, heartbeat, TTL) checked before every step; restore and migrations hold them. |
| Executor | `work.executors` | A process that runs Jobs, heartbeating its role, lanes and in-flight writes. |

Kinds, lanes, states and priorities are closed sets. `core.work_priority` owns
`WorkPriority`; `db.models.types` re-exports it with the other enums. They are
stored as TEXT with a CHECK constraint listing their values
(no database-native enum types, see the database skill reference). A kind
with no member cannot be declared, recorded or requested, and
`tests/integration/bootstrap/test_work.py` fails when a member has no
definition.

Definitions without a source (uploads, backups on request, archive imports) are
requested: a route records the Job with its intent in one transaction and
nudges. Definitions with a source are discovered: the source finds the work.

## Engine guarantees

The port (`work.contracts.JobEngine`) executes two kinds of submission, a
`JobSubmission` (one attempt of one Job) and a `PassSubmission` (one reconcile
pass over one source), and names exactly two guarantees, both provided by DBOS:

- `execution_id = <job id>:<epoch>:<attempt>`: one execution per attempt and epoch, ever.
- `dedupe_key = <definition>|<subject>`: at most one active execution per
  subject. A Job is routed either `Deduplicated` by that key or, on a
  partitioned lane, `Partitioned` by its definition's partition key (DBOS
  cannot deduplicate a partitioned queue, so there the active-subject index
  is the claim). The catalog refuses a definition whose partition rule does
  not match its lane.

Nothing else is assumed of the engine. Its state is disposable: SQLite keeps it
in `printstash-dbos.sqlite` beside the vault database; PostgreSQL keeps it in
the `dbos` schema of the same database. Backups do not include it.

## The reconciler

One pass (`work.reconciler.run_pass`) per definition, claimed through its
`reconcile_cursors` row so two processes never run the same source at once:

1. **Repair.** Active Job rows are compared with the engine's evidence for
   their current attempt, and the pure `decide()` returns none, complete,
   resubmit, interrupt or fail. Work of another application version, or
   stranded on an executor not proven alive (its heartbeat lapsed, or it has
   no row at all), is cancelled and rerun. A process registers its executor
   before its engine launches, so its own work is never mistaken for lost.
   A Job resubmitted more than `JOBS_MAX_RESUBMITS` times fails.
2. **Discover.** The source is asked for at most
   `min(JOBS_RECONCILE_BATCH, lane headroom)` items, where headroom is the
   lane's concurrency × `JOBS_LANE_HEADROOM_FACTOR` minus its queued depth for
   ordinary sources. Derivative sources receive explicit priority allowances:
   interactive queued/interrupted Jobs are capped at lane concurrency; all
   active backfill Jobs, including running ones, share one slot. Every
   definition in a lane shares those caps. Each item becomes a Job (unless its
   subject already has one) and is submitted. A short lane fence locks the
   quota recheck and Job creation in the same transaction; engine submission
   follows commit. A lost or expired holder cannot admit work.
3. **Continue or stop.** A full batch that created Jobs while the lane still had
   room runs again immediately. A full lane stops; each Job's completion nudges
   its source. Starting an interactive Job from a prioritized source also nudges
   it, because running interactive Jobs no longer consume the pending allowance.
   That lets a successor queue during the current attempt without enlarging
   either priority's cap. A nudge that arrived during the pass (the dirty mark,
   `nudged_at`) makes the pass run once more before it releases.

A completion always nudges the definitions it names (`completion_nudges`). It
nudges its own definition when that definition has a source, or when another
active Job of it has yet to reach the engine (never submitted, interrupted, or
on an epoch its last submission did not carry). Request-originated work with
nothing waiting to be submitted has nothing a pass could find or submit, so no
pass is queued for it; the tick repairs what a crash left behind.

**Additions first.** A definition that brings Models into the library (an
upload, an import, a library scan) declares `adds_to_library`; derived outputs
(thumbnails, measurements, G-code and toolpath previews) and search projection
declare `waits_for_additions`. While any Job of an adding definition is active, a
waiting definition's discovery finds nothing new, so a large import shows all
its Models before their derived work starts competing with it. Jobs already
created keep running. The last adding Job to settle stamps every waiting
definition in its own terminal transaction; the tick recovers a lost nudge.
The viewer mesh does not wait: opening a Model in 3D asks for it.

A nudge (`work.nudge`) stamps the dirty mark and queues a pass unless one is
already queued within `JOBS_SUBMIT_GRACE_SECONDS`. An interactive nudge is not
absorbed by a queued backfill pass. The tick, a single persistent DBOS
schedule, nudges at `JOBS_RECONCILE_INTERVAL_SECONDS` as a safety net every
definition a pass could find work for: each one with a source, and a requested
one only while one of its Jobs is active (otherwise its pass would read two
empty results and stay in the engine's history). Schedule sources become due
on it. Its two lost-work sweeps share one read of the engine and the executors.

A submission is one engine write: a reused execution id is refused by its own
insert and a dedupe key another workflow holds names that holder, so no status
is read before enqueuing.

A request that records a Job it will wait on (an upload, a URL import)
dispatches it itself after its commit: it submits the Job through the same
path a pass uses, so no pass is queued to find it. Anything short of an
accepted submission falls back to a nudge, and the repair pass remains the
recovery. Because the engine finds queued work only by polling, that
submission also boosts its lane's polling for a few seconds: the Job is picked
up sooner, and so is the rest of a batch that follows it.

On SQLite, accepted immediate interactive Jobs on FIFO lanes also use that
temporary boost, as do the reconcile passes discovering their work. Duplicate,
delayed and backfill submissions do not start or renew this automatic boost.
SQLite's process lock and topology guard ensure a single engine owner. The
extension does not apply to PostgreSQL: its queue configuration is shared by
multiple processes, while boost expiry is local to each process. Existing
explicit request boosts keep their current policy. A boost changes polling
configuration through the engine SDK; it does not interrupt a poll already
waiting, and is a latency hint rather than an execution guarantee.

The dirty mark is a write, so it travels with a write that already happens.
`nudge_after_commit` stamps it as the caller's transaction commits, and a Job's
terminal write stamps the sources its completion affects; only the pass is
queued afterwards. A rolled-back transaction leaves no mark. A process that
dies between the commit and queuing the pass leaves a mark that covers further
nudges for the grace window, after which the tick queues the pass.

A step's cancellation checkpoints come in two kinds. A forced checkpoint, the
kind that guards a publication, reads its own Job afresh. A paced checkpoint,
the kind a step calls while it waits for a worker, a permit or a lane, is
answered from one read shared by every step the process is running, about a
quarter of a second old at most (a step that probes while another is reading
takes the last answer): waiting steps cost one read between them rather than
one each. The shared read never holds a lock other steps wait on.

## An attempt's writes

A Job attempt writes as rarely as its guarantees allow:

- **Begin** claims the attempt and, for interactive work from a prioritized
  source, registers an after-commit nudge. On SQLite its dirty mark commits
  with the claim, without a second writer transaction.
- **Progress** a step reports in the first `JOBS_PROGRESS_HOLD_SECONDS` of its
  attempt is held in memory and merged into the next write, so an attempt that
  ends sooner never pays for reports nobody could read. Past the hold each
  report is written when it is made. Held progress is lost with the process;
  steps never depend on it.
- **The end** is one transaction: the terminal state, the subject's failure
  hook and the completion marks. A step that no longer needs its staged input
  ends with `ctx.settle(...)` instead of `ctx.finish(...)`, which puts the
  definition's settled cleanup in the same transaction. Otherwise the cleanup
  runs once the last step returns. A definition without a settled cleanup
  takes no transaction for it.

Every part is idempotent, and what an execution has already written is known
only in memory: an execution recovered after a crash settles in full again.

## Derivative scheduling

`derive.native` and `derive.light` use one FIFO engine queue each, with equal
engine ranks for both logical priorities. This gives admitted backfill a finite
predecessor list under sustained uploads, while preserving each lane's
concurrency, rate and partition limits. Their pending headroom is concurrency
plus one; other lanes keep strict engine priority and the configured headroom
multiplier. Logical priority remains on the Job and is bound around each actual
step in its execution thread.

Native CPU/RAM, prepared-source bytes and source I/O use shared physical
resource pools. Interactive waiters precede unaged backfill; backfill waiting
30 seconds joins the interactive rank in registration order, allowing a large
claim to accumulate enough free capacity. Active work is never preempted.
Waiting for capacity does not create a new Job or derivative attempt.
The supervisor passes the logical priority into each disposable native child;
an exec boundary cannot promote backfill to interactive. Asynchronous mesh
thumbnail workers separately mark render dispatch as batchable background work,
including freshly uploaded Artifacts. This permits the compute broker to group
ready models for up to 10ms without changing their Job FIFO position or native
resource tier. Direct interactive render callers retain immediate dispatch.

Recovery selects one active backfill per shared FIFO lane, preferring running
work, then already reserved or accepted epochs, then oldest unsubmitted intent. Other retained intents wait without spending attempts. The owner
selection is cached for the repair batch and refreshed after its owner settles.
If update ordering places that owner outside the ordinary page, repair includes
at most one additional Job belonging to its own definition. This preserves the
batch-plus-one bound and prevents retries from hiding the only admitted backfill.
Before each backfill submission, a conditional owner UPDATE under the live lane
fence commits its admission epoch without spending an attempt. Submission checks
that exact current reservation before constructing engine arguments. An accepted
execution whose attempt was not yet recorded retains its slot across a crash;
terminal state or a public retry epoch change retires the old authority. The
active-state predicate uses the existing partial active-subject index, so
terminal Job history cannot enlarge discovery or quota scans.

Before enabling FIFO consumers, startup refuses persisted derivation workflows
with legacy engine ranks or queue state it cannot verify. Upgrades drain old
producers and restart API and workers together, as described in the
[upgrade guide](../../UPGRADE.md#unreleased-derivative-scheduling).

## Batch imports

Ingestion Job Definitions consume a durable source plan through `BatchSession`.
`batch_contracts` owns typed source identities and outcomes; `batch_store` owns
fenced SQL persistence. Source adapters materialize one disposable window, then
use the canonical Artifact commit before releasing that window. An
`IngestionEntry` success commits with the File, Metadata and provenance changes;
reusing an existing File still records a distinct source receipt.

A Job owns ordinary URL/archive entries. A Pending Import owns its entries
across replacement retry Jobs. Writers check the exact execution attempt and
epoch, plus the Pending Import's current Job. Registration order never enters a
new entry's identity. Retry consults confirmed receipts before source I/O.
Discovery totals stay unknown until expansion finishes, while processed and
successful counters advance for each entry. Source failures remain separate
from already committed Artifacts.

Each import holds at most one expanded/copied disposable file. Downloaded
archive containers and durable upload leases retain their own existing bounds.
Cleanup failure must stop further materialization; it does not revoke an
Artifact already committed. Derivatives can run through their existing Jobs
while later source entries are processed. This does not introduce a second
queue, concurrent SQL writers or parallel downloads.

## Derivatives

See [derivatives.md](../derivatives.md). In short: three producer groups
(`derivatives.mesh`, `derivatives.gcode`, `derivatives.toolpath`) pull Artifacts missing a kind
at its recipe version through a bounded anti-join, one mesh load produces
geometry and the thumbnail together, and each kind's row records ready,
skipped, failed (with backoff) or cancelled.

## Realtime notices

`work.events` publishes ids and a hint, never protected data:

- `jobs:<user id>` for the owner's Jobs, `work:admin` for every Job;
- `model:<id>` when a derivative of one of the Model's Artifacts changes.

`GET /api/v1/events/ws` (one-use ticket from `POST /api/v1/events/ticket`)
delivers them. The client follows a Model with `{"subscribe": "model:<id>"}`;
the server checks the viewer may see it and answers
`{"type": "subscribed", "channel": "model:<id>", "revision": "<opaque>"}`, the
cue to refetch what changed before the subscription took effect. `revision` is
`records.model_revision`: it differs whenever a derivative of the Model's live
Artifacts was created, removed or moved to another state, and `ModelRead`
reports the same value as `derivatives_revision`. A client whose snapshot
already carries the acknowledged revision missed nothing and does not refetch;
the value orders nothing and other channels carry none. Delivery is best effort: in one
process it is in memory, across processes it is PostgreSQL `NOTIFY`, and a
reconnect delivers `{"type": "resync"}` so clients refetch.

Job notices include `task_visible`: true for user-owned Jobs and scheduled
backups, matching the default `/jobs` list. The shared administrator socket also
carries maintenance with this flag false. Task Center ignores those notices;
the Background work page still refreshes. Reconnection always resynchronizes,
and clients retain the old wake behavior when an older server omits the hint.

## Topologies

| Topology | Settings | Needs |
| --- | --- | --- |
| Unified (default) | `VAULT_PROCESS_ROLE=all` | SQLite or PostgreSQL, any storage |
| API plus workers | `VAULT_PROCESS_ROLE=api` (API also runs jobs), `worker` replicas | PostgreSQL, a shared volume |
| API without jobs | `VAULT_PROCESS_ROLE=api`, `VAULT_API_RUNS_JOBS=false`, `worker` replicas | PostgreSQL, a shared volume |

A worker is `python -m app.worker`: no HTTP, no printer or folder supervisors;
it waits until the API has migrated the schema, then runs every lane.
"Shared storage" is one volume mounted by every process at the same paths and
declared with `VAULT_SHARED_STORAGE=true`; startup refuses a split topology
without it. Uploads are staged on local disk whatever the storage backend, and
the worker that commits one reads what the API staged, so the staging directory
is always on it; with local storage the vault and thumbnails are too.
`docker-compose.advanced.yml --profile workers` runs the second and third
topologies (see [deployment](../deployment.md#background-work-and-workers));
both Compose files run the first with no setting.

## Configuration

| Setting (`VAULT_…`) | Default | Meaning |
| --- | --- | --- |
| `JOBS_QUEUE_POLL_INTERVAL_SECONDS` | 1.0 | DBOS lane dispatch polling (0.05–5 seconds) for derive, network, printing and reconcile lanes; lower values trade more local database polling for shorter queue handoffs |
| `JOBS_INTERACTIVE_QUEUE_POLL_INTERVAL_SECONDS` | 0.25 | Polling of the ingest lane, where an accepted upload or import waits for its Job to start; never slower than the interval above |
| `JOBS_RELAXED_QUEUE_POLL_INTERVAL_SECONDS` | 2.0 | Polling of lanes nobody waits on (search, maintenance, notify, similarity, captions, expansion); never faster than the interval above. Every look at a queue costs database statements whether or not work is there |
| `JOBS_BOOSTED_QUEUE_POLL_INTERVAL_SECONDS` / `JOBS_QUEUE_BOOST_SECONDS` | 0.1 / 3.0 | A lane a request has just dispatched a Job to is polled at the first for the second, then returns to its own interval; a window of 0 disables it |
| `JOBS_RECONCILE_INTERVAL_SECONDS` | 300 | Safety-net tick. Runs as a cron, so an interval that does not divide the minute, hour or day evenly is rounded down to one that does (90 runs every minute) |
| `JOBS_RECONCILE_BATCH` | 500 | Most Jobs one pass creates |
| `JOBS_LANE_HEADROOM_FACTOR` | 2 | Pending depth multiplier for ordinary lanes; derivation lanes reserve concurrency plus one |
| `JOBS_MAX_RESUBMITS` | 3 | Interrupted attempts before a Job fails; also how often the engine recovers one execution after its process died (with a pinned `EXECUTOR_ID`) |
| `JOBS_RESUBMIT_COOLDOWN_SECONDS` / `JOBS_RESUBMIT_BURST` | 30 / 3 | A subject finished this often waits the window out |
| `JOBS_SUBMIT_GRACE_SECONDS` | 60 | How long a queued pass absorbs further nudges |
| `JOBS_PROGRESS_HOLD_SECONDS` | 1.0 | How long an attempt runs before the progress it reports is written (0–60 seconds; 0 writes every report) |
| `JOBS_EXECUTOR_STALE_SECONDS` | 120 | Heartbeat age after which an executor's work is rerun |
| `JOBS_RETENTION_DAYS` / `JOBS_RETENTION_PER_USER` | 7 / 500 | User Job history |
| `JOBS_SYSTEM_RETENTION_HOURS` | 24 | Ownerless (backfill) Job history |
| `ENGINE_HISTORY_RETENTION_DAYS` | 7 | Settled engine executions |
| `DERIVATIVE_MAX_ATTEMPTS` / `DERIVATIVE_BACKOFF_SECONDS` | 5 / 30 | Derivative retries, doubling, capped at a day |
| `FENCE_HEARTBEAT_SECONDS` / `FENCE_TTL_SECONDS` | 15 / 60 | Fence liveness |
| `JOBS_<LANE>_CONCURRENCY` | ingest 2, derive.light 4, network 4, similarity 1, notify 1, printing 1, maintenance 1, search 1, captions 1, expansion 1 | Per-lane concurrency; `derive.native` defaults to `MAX_RENDER_JOBS` |
| `JOBS_NOTIFY_RATE_PER_MINUTE` | 30 | Deliveries per channel |

Administrators override lane concurrency at runtime on Settings → Background
work; the override is stored in the database and applies to every process.
The same page sets the native mesh job count (`max_render_jobs`, stored in
`system_config`): the process that saves it re-resolves the `derive.native`
lane at once, a lane override still wins, and worker processes read it when
they start.

Native memory is bounded by shared CPU/RAM admission across API and worker
processes, independent of lane count. A request reserves its complete geometry,
raster and analysis profile; admitted credits remain held through descendant
termination. Preparation separately reserves source-copy bytes and I/O slots.
Each native child is killed past its memory budget. Within one process, local inference (embedding
and search-view workers) is also admitted locally, up to `MAX_RENDER_JOBS` at
once, because a search query embeds in the request, outside every lane: a
waiting query goes first, and background inference that would wait yields
(`embedding_compute_busy`) and its Job's next pass retries it. No permit is
stored in the database.

AI Search work is durable intent, found like any other:

| Definition | Source (intent) | Lane |
| --- | --- | --- |
| `search.project` | `SearchProjectionRequest` rows each library change records | `search` |
| `search.generation` | one Job per building `IndexGeneration`; the Job an administrator follows | `search` |
| `search.index` | active generations with passages to index, retired ones to prune | `search` |
| `search.repair` | every five minutes while search is on: passage, lexical and vector repair | `search` |
| `search.caption_queue` / `search.caption` | Models owed a caption / one Job per caption attempt | `captions` |
| `search.expand` | visible passages owed a sparse expansion | `expansion` |
| `inference.model_download` | one administrator-requested download at a time | `network` |

Their units keep their row leases, which fence publication by a superseded
attempt, and admit themselves one at a time, so a restore drains between two
units and a user's write in flight goes first: a unit waits a few seconds for
that write rather than ending its Job, and never waits for a restore. A build
holds the `search` lane for as long as it runs, so each of its turns also
projects a batch of library changes. Downloads share one subject, so the
active-subject index keeps them one at a time. Warming the query models is the
exception: a warm model is memory in the API process's own worker pool, so a
supervisor thread in that process keeps it warm rather than a Job.

A restarted API reruns what its predecessor left running at once, not after
the stale window: holding the vault's API lock proves the earlier API process
is gone, so its executor is marked stale at startup. Workers share no lock and
are only ever recognised by their heartbeat going stale.

## Failure and recovery

- **A process dies mid-step.** Its executor stops heartbeating; after
  `JOBS_EXECUTOR_STALE_SECONDS` a pass sees the execution stranded, cancels it
  and resubmits the next attempt. Derivative rows it left running go stale and
  are offered again.
- **A clean stop outlasts a step.** Shutdown waits ten seconds for running
  executions, then deletes its executor's row. A step still running stays
  running in the engine, which only recovers its own executor's work; the next
  pass sees an executor with no row, cancels the execution and reruns the Job.
- **The database refuses a write for a moment.** Claiming an attempt and
  recording its outcome retry a busy SQLite file or a PostgreSQL restart (up to
  five tries over about fifteen seconds). Only a refusal that outlasts them
  ends the execution in error, which fails its Job.
- **A running Job is cancelled.** Its execution is left to stop on its own: the
  step sees the cancelled Job at its next check and ends. The engine is told to
  cancel only queued or delayed executions, because cancelling a running one
  frees its lane slot while its step is still running. A step that never
  checks would hold the slot forever, so the tick cancels any settled Job's
  execution still running `JOBS_EXECUTOR_STALE_SECONDS` after it settled.
- **A restore drops a live executor's row.** A restored snapshot need not hold
  every running process. A process registers again on its next heartbeat when
  its row is gone, since other processes rerun the work of an executor they
  cannot see.
- **Upgrade.** The new version cancels executions of the old one at startup and
  reruns them on the new code, without waiting for staleness.
- **Restore.** A restore holds the restore fence, so no step starts; afterwards
  the engine's state is discarded and every definition reconciled against the
  restored database. The snapshot's Jobs describe an engine that no longer
  exists: a Job whose definition sets `survives_restore=False` (a backup, which
  the snapshot shows mid-archive) is cancelled through its cancel hook, and the
  snapshot's queued-pass marks are forgotten so they cannot suppress the
  reconcile. Everything else the snapshot says is owed is simply found again.
  A PostgreSQL vault restored from the built-in portable snapshot, with the
  `dbos` schema dropped or stale, converges the same way on its next start.
- **Recovery resolves a restore.** A process that starts while an interrupted
  restore or Vault migration journal still governs holds its background work
  (`bootstrap.work.hold`). Recovery does not restart the process, so whatever
  resolves the journal (`after_restore`, a migration's recover) calls
  `release_held`, and the work starts in place.
- **A run's own record.** A similarity run, backup run or destination retry is
  one Job's subject. The engine alone decides what runs: the run keeps no lease,
  only a write fence naming the execution allowed to publish (a similarity
  run's `writer`), and the next attempt of an interrupted Job settles what the
  previous attempt left open.
- **A nudge is lost.** Nothing happens until the next tick or completion nudge;
  the intent is still in the database.
- **A notice is dropped.** Clients also refresh on a slow interval and on
  `resync`.

## Retained ingest input

JobStatus includes a nullable staging summary (retained_bytes, lease_count,
earliest_expiry, discard_available). It never includes storage paths. Failed,
uncommitted ingest input remains charged until the configured lease retention
expires or the owner explicitly discards it. Active input survives expiry.
Completed uploads are reconciled against their committed Artifact on settlement
and startup, releasing exact receipts promptly.

POST /api/v1/jobs/{job_id}/discard-staging returns 204, including when already
absent. Only the owner or an administrator may use it, and only terminal ingest
jobs are eligible. Active work and uncertain ownership return 409; another user
sees 404. Discard and retry lock the same Job row (SQLite reserves its writer),
so retry cannot race deletion. Replacement or inaccessible paths remain charged.
Capture-origin inputs retain their Pending Import dismissal lifecycle.

Tasks show retained capacity and expiry with a confirmation before discard.
After discard, retry requires the input to be uploaded again.

## Producer policy admission

Job Definitions may provide an engine-independent `admission(Session)` hook.
The coordinator consults it before discovery, Job creation, submission and
actual step execution. Derivative definitions resolve live database policy.
Their producers serialize final admission with configuration updates and check
again whenever actual processing executes after a recovery.

Disabled derivative sources yield no subjects or retry deadlines. A bounded
repair pass drains queued and lost attempts as policy cancellations without
withdrawing domain intent; active, healthy processing may finish. The ordinary
tick remains the recovery mechanism when engine cancellation or a realtime hint
fails. Policy cancellations are excluded from submission-burst cooldown.

Background work exposes effective/default/override state for each derivative
definition. Historical failures remain inspectable but disabled groups do not
contribute to actionable failures or retry lists.

## Synchronous native cancellation

WorkRunner scopes each synchronous step to its Job attempt. Native supervisors
and their shared admission controller check durable withdrawal through
app.core.cancellation; a cancelled or superseded attempt releases its process
tree and owned resources before returning a cancelled step outcome. The scope
is reset even when the step raises. Concurrent steps have independent probes.
A forced check before accepting native output prevents a result finishing inside
the normal 200 ms polling interval from publishing after observed withdrawal.
The Job engine remains responsible for durable dispatch and settlement.

### Execution authority across retry

`Job.attempts` counts engine-accepted executions, including a worker that began
before the submitter recorded acceptance. Explicit retry preserves this count
and rotates `execution_epoch` atomically with the subject retry hook.
`submitted_epoch` records the last accepted generation (null before the first
submission). A mismatch is durable pending intent: reconciliation submits it
immediately, even if the retry nudge was lost. It never treats that state as a
lost execution or consumes the interruption budget.

Engine identity is `Job ID:execution_epoch:attempt`. Both engines carry the epoch
through begin, progress and settlement; legacy callbacks without an epoch are
ignored. `JobStore.lock_execution` validates authority under the Job row lock.
Failure hooks share the first terminal transaction; cleanup replay additionally
checks epoch, attempt and terminal state, so it cannot reclaim retry-owned staging.
Submission uses a snapshot, engine I/O, then a conditional database write. A late
engine acknowledgement cannot overwrite a retry's count or timestamps.

If the first submission of a new epoch is deduplicated, it checks the engine's
active catalogue for obsolete executions of this subject's terminal Jobs or old
epochs of the current Job, cancels those, and retries submission once. The engine
port has no subject lookup; this catalogue read is limited to actual deduplication
conflicts. Healthy submissions do not scan it. No database lock spans engine I/O.
