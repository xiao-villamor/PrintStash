# Background work

Adding or changing a Job Definition, Work Source, Step, Lane or Derivative
kind, and how to test it.

Binding background: [ADR 0008](../../../../docs/adr/0008-job-engine.md),
[docs/architecture/background-work.md](../../../../docs/architecture/background-work.md),
[docs/derivatives.md](../../../../docs/derivatives.md). Terms (Job, Subject,
Work Source, Nudge, Lane, Fence, Derivative, Recipe) are in `CONTEXT.md`.

## Before you add anything

- Work a request would otherwise do inline is a Job. A value computed from an
  Artifact's bytes is a **Derivative kind**, not a Job of its own.
- Intent lives in your module's tables (or the Job row for requested work),
  never in engine input. Steps rebuild what they need from the database.
- Never import `dbos` outside `app/runtime/engine/`; never call
  `BackgroundTasks`, `asyncio.create_task` or a `to_thread` loop for work.
  `tests/repo/test_forbidden_imports.py` enforces both.

## Add a Job Definition

1. Add the kind to `JobKind` in `app/db/models/types.py` (`<owner>.<verb>`,
   in its owner's block) and to the `JobKind` union in
   `frontend/src/types/models.ts`, then autogenerate the migration that
   re-creates the `jobs.kind` and `reconcile_cursors.source` CHECKs
   (`tests/repo/test_frontend_enums.py` and `test_models_versus_chain` fail
   until both exist).
2. In `app/modules/<owner>/jobs.py`, build a `JobDefinition` with that
   `JobKind` as its `name`, a `LaneName` as its `lane`, ordered `Step`s, and a
   `label` for the admin page. A step finishes its Job only with a
   `JobOutcome`; a `FAILED` outcome always carries an `error`.
3. Declare what cancel does to the Subject (`cancel`), and, when meaningful,
   `retry` (return `False` when the Subject is gone) and `on_failure`. A
   cancel that does not withdraw intent is a bug: the reconciler resubmits it.
4. Requested work: record the Job with `work.service.request(...)` in the same
   transaction as its intent, commit, then `nudge(definition)`. Discovered work:
   give the definition a `source` (below) and nudge from the hot path.
5. Register it in `app/bootstrap/work.py:definitions()`.
6. Steps must be idempotent: a crash re-runs a step, and a resubmitted attempt
   re-runs all of them. Check `ctx.cancelled()` between units of long work, and
   report progress with `ctx.update(...)`. Progress is display only: reports
   made in the first second of an attempt are held and written with the next
   write, so never read a Job's status back to learn what a step did.
7. A step that ends its Job and no longer needs what `on_settled` releases
   calls `ctx.settle(outcome, ...)`; one that still holds its staged input
   calls `ctx.finish(...)` and lets the cleanup run after the last step.

## Add a Work Source

- `StateSource.pending(session, now, limit)` returns at most `limit`
  `WorkItem`s from one bounded, indexed query per stage. Never loop per row, and
  never return a Subject that already has an active Job (the index refuses it
  anyway, but the pass wastes its budget).
- Choose `WorkPriority.INTERACTIVE` only when a user is waiting.
- `next_due(session, now)` tells the tick when time alone makes work pending
  (a backoff expiring); return `None` otherwise.
- `ScheduleSource` for cadences; the Subject renders the occurrence, and a
  missed occurrence is superseded, not replayed.

## Lanes

Reuse a lane unless the work has a genuinely different cost profile. A new lane
is a `LaneName` member (and its frontend union and CHECK migration), a
concurrency setting in `core/config.py` (`JOBS_<LANE>_CONCURRENCY`), an
entry in `catalog.default_lanes()`, and docs in `background-work.md`.

## Derivative kinds and recipes

Follow "Adding a kind" in `docs/derivatives.md`. Bump the kind's recipe
constant in `derivatives/kinds.py` in the same change that alters what its
producer emits; never for a refactor that cannot change output.

## Testing

Load [testing.md](testing.md) first; the matrix rules apply. Tiers:

| Tier | Engine | Use for |
| --- | --- | --- |
| `unit/modules/work/` | none | Pure logic: `decide()`, subject rendering, schedule due-ness |
| `integration/` | `InlineJobEngine` (autouse `work_engine`) | Sources' `pending`, steps, cancel/retry hooks, routes that accept work |
| `integration/postgres/` | inline, real PostgreSQL | Claims and FK actions under concurrency |
| `contract/modules/work/` | inline **and** DBOS | Anything the port promises (run the shared contract) |
| `e2e/` | inline in-process; DBOS in subprocesses (`tests/fakes/job_engine_process.py`) | Headline flows; crash, upgrade and split-topology recovery |

Patterns:

- Nothing runs until the test calls `work_engine.drain()` (or `run_one()`), so
  "accepted" and "happened" are separate assertions. `drain_work()` in
  `tests/integration/api/v1/_ingest_assertions.py` does the same from a route
  test, and `tests/e2e/_jobs.py` from e2e.
- Build rows with `make_job`, `make_derivative`, `make_work_fence`,
  `make_work_executor`; a Job's `kind` must be a registered definition.
- Assert the engine side through `work_engine.executions[execution_id(job, n)]`.
- Monkeypatching a producer after the catalog is built has no effect (the
  session-scoped catalog captured it); patch what the producer calls instead.
