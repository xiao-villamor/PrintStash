# Backend module boundaries

PrintStash is a modular monolith. FastAPI is the HTTP adapter, SQLModel and
Alembic own persistence, and the default deployment remains one process with
SQLite and local storage. PostgreSQL, S3 and OpenDAL are optional adapters.
Moving a responsibility does not change its database schema, storage keys,
HTTP endpoint or persisted archive format.

## Ownership

File acceptance crosses the HTTP async boundary through
`api.command_execution`. A bounded command creates, uses and closes its SQL
Session on one worker thread and builds its response before closing. Authentication
returns an immutable `api.command_actor.CommandActor`; the command rechecks
credential and current user authority inside its own Session. ORM rows and Sessions
never cross a streamed body wait. Capture spool contexts retain only an inode
receipt and an open descriptor.

`VAULT_API_COMMAND_CONCURRENCY` bounds these commands separately from FastAPI
ordinary workers (default 8, range 1–128 per API process). Slow SQL, storage or
engine hints consume this capacity while lightweight requests can still progress.
Queued cancellation starts no write; an admitted callable finishes before its
cancellation returns. An awaited AnyIO task group joins the command worker even
when the server cancels its native request task, preserving capacity and exact
callable failures. This is awaited request execution; background processing
continues through Job Definitions after durable intent is committed.

The [request command matrix](../testing/api-command-execution.md) covers real
Session ownership, streamed body waiting, credential changes and responsiveness.

| Owner | Responsibilities | Public operations and contracts |
| --- | --- | --- |
| `library` | Models, Artifacts, G-code Revisions, taxonomy, multipart sets, provenance and trash | `commands`, `revisions`, `model_views.{listing,pagination,detail,facets,exports,statistics,trash}`, `multipart_models`, `part_options`, `provenance`, `source_covers`, `taxonomy`, `saved_views`, `trash` |
| `ingestion` | Accepting bytes, staging, Inbox review, URL imports and portable transfer | `ingestion.persist_artifact`, `requests`, `jobs`, `background`, `inbox`, `importer`, `library_transfer`, `staging_leases`, `staging_cleanup` |
| `sources` | Mounted/remote libraries, discovery, root enrollment and watching | `contracts`, `root_binding`, `library_source`, `external_library`, `library_watcher`, `library_locations` |
| `storage` | Object identity, ownership, publication, reading and verified deletion | `storage_backend.contracts`, `artifact_content`, `storage_ownership`, `storage_deletion`, `storage_operations`, `storage_connections`, `storage_paths`, `storage_providers` |
| `backups` | Snapshot creation, catalogue, verification, replicas and journaled restore | `backup.creation`, `backup.catalogue`, `backup.verification`, `backup.adoption`, `backup.deletion`, `backup.restore`, `backup.recovery`, `backup_runs`, `retry_commands`, `backup_schedule` |
| `printing` | Printers, provider adapters, fleet scheduling, materials and print history | `dispatch`, `costing`, `printer_provider`, `printer_hub`, `fleet`, `materials`, `printer_files`, `printer_jobs`, `print_results`, `multipart_builds` |
| `similarity` | Versioned geometric evidence, indexed retrieval, durable analysis runs and explicit review | `fingerprints`, `retrieval`, `processing`, `candidates`, `review`, `composition` |
| `inference` | Shared provider contracts, model identity and inference runtimes | `local`, `manifest`, `onnx_cpu` |
| `media` | Mesh processing, thumbnail rendering and publication, source covers and toolpath conversion | `mesh_contracts`, `thumbnail_engine`, `thumbnail_publication`, `toolpath`, `source_cover_processing` |
| `derivatives` | What each Artifact owes (metadata, thumbnail, toolpath), found by anti-join and produced by Jobs | `kinds`, `records`, `source`, `producers`, `repair`, `jobs` |
| `work` | The engine-agnostic background work model: Jobs, definitions, sources, the reconciler, fences and realtime notices | `contracts`, `catalog`, `jobs`, `sources`, `reconciler`, `submission`, `runner`, `service`, `fences`, `executors`, `events` |
| `identity` | Product identity, collection/printer authorization, sharing and tickets | `auth`, `oidc`, `rbac`, `printer_rbac`, `share`, `ws_tickets` |
| `notifications` | Notification preparation and delivery | `notifications`, `notification_renderers` |
| `administration` | Dynamic OSS settings, setup, audit and operational inspection | `runtime_config`, `setup_policy`, `setup_bootstrap`, `setup_storage`, `audit`, `vault_audit`, `release_check` |
| `runtime` | Process coordination: maintenance admission, the job engines and event transports | `maintenance`, `engine.dbos_engine`, `engine.inline`, `realtime` |
| `db` | Session factories, SQL schema and metadata registration | `session`, `scopes`, `models`, `publication`, `transactions` |
| `search` | Authorized passage projection, lexical retrieval, immutable embedding spaces and vector generations (AI Search in progress) | `sources`, `passages`, `projection`, `retrieval`, `vector_store` |

The table identifies interfaces, not permission to reach through an operation
into its implementation. A function prefixed `_` belongs to its owner. Public
operations express a capability; do not expose a private helper merely to make
an import check pass. A module may keep several files when they describe one
cohesive operation. Do not create empty repository/service/manager classes to
fill a template.

A module's imports are implementation details, not automatic public exports.
Routes import configuration, filesystem utilities and provider contracts from
their actual owners; they must not reach them through an operation such as
`inbox.settings` or `inbox.staging_leases`. A deliberate public re-export must
be named in `__all__`. The architecture check enforces this boundary for HTTP.

Mesh request/result types and tagged geometry codecs belong to
`media.mesh_contracts`. Workers, supervisors, producers and tests import those
data contracts directly; `thumbnail_engine` owns strategy selection and cleanup.
Primitive mesh and scene owners cannot import an orchestrator, including solely
for annotations. The contract module imports no loader or analysis owner at
runtime, so importing a request type cannot initialize those execution paths.

## Dependency rules

- HTTP validates transport input, obtains a trusted actor/context, calls an
  operation, and translates its result or business error. Workers call the same
  operation and must pass the same authorization checks.
- Product operations may use their concrete database adapters. Shared rules
  cannot import `app`, FastAPI, SQLModel, SQLAlchemy or cloud infrastructure.
- Shared operations receive small operation-specific ports and immutable
  snapshots/results. An ORM row must not cross into `printstash-core`.
- The operation owns its SQL transaction. Helpers stage or flush changes;
  independent commits must represent a separately documented durable operation,
  such as a publication intent before writing bytes.
- A read module owns its SQL projections. Keep batched loading and query-count
  tests; replacing SQL with repeated generic repository calls is not an
  architectural improvement.
- Infrastructure is constructed at startup and passed to its consumers.
  Compatibility accessors may return a bound adapter, but must not let the first
  caller construct infrastructure from mutable settings.
- A lower-level contract cannot import the workflow it supports. Root marker
  parsing lives in storage, source binding in sources, and scanning coordinates
  ingestion. Remote discovery consumes source contracts rather than the source
  adapter. Thumbnail orchestration uses mesh primitives, never the reverse.
- Database tables are declared by domain under `app/db/models/`. Every table
  module obtains `SQLModel` from `models.base`, so naming conventions exist
  before any table is registered. The package registers and exports the complete
  model set; migrations retain their original contents.

`backend/scripts/architecture.py` checks deferred as well as top-level imports.
`architecture-debt.json` has no remaining exceptions. Both the command and repo
tests reject adding new exceptions. They also reject cycles, private cross-module
dependencies, FastAPI/HTTP transport imports in capability operations, and imports
from the removed `app.services` tree. `core.errors.OperationError` carries business
failure kinds and optional retry delays; `api.errors` alone maps these to HTTP
statuses and headers. Browser cookies and setup tickets belong to
`api.session_cookie` and `api.setup_session`. `administration.setup_policy`
resolves `VAULT_SETUP_MODE` and `VAULT_SETUP_ADMIN_*` into the one first-run
policy that every door checks; first ownership (browser claim and provisioning at
startup) and installation locking live in `administration.setup_bootstrap`, and
first-run storage preparation in `administration.setup_storage`.
Type-only imports do not create runtime cycles.

The cycle check operates on Python implementation modules, including deferred
imports. Capabilities can cooperate through public operations in both directions;
the ownership table is not a claim that the coarse capability graph is a DAG.
The explicit historical path map is `backend/module-map.json`.

## Shared business and product adapters

`backend/packages/printstash-core` is canonical. Cloud adoption is deferred to
[the separate implementation guide](cloud-adoption.md). Its adapter must adopt an exact upstream
revision through its existing pinned-source workflow. Matching interface names
alone are not a compatibility claim. A pin must identify the entire copied
package, including contract examples and tests.

The first shared operation is `printstash_core.library.delete_revision`.
Its unit of work loads an authorized live Model and its live Artifacts, stages
soft deletion, promotes the highest-version surviving G-code when the deleted
Revision was recommended, clears a thumbnail only when it belonged to that
Revision, and commits once. OSS executes the examples in `printstash_core_testkit.revisions` against its real
persistence adapter. Cloud must execute the same examples when it adopts this port.

The initial Similar Models geometry operation is
`printstash_core.mesh.similarity.fingerprint_mesh`: bounded analysis over loaded
arrays with no file, database, framework or inference dependency. Its partial
results are retrieval evidence, never Model identity. The planned product
similarity owner and its API/worker adapters are not yet implemented; see
[ADR-0005](../adr/0005-similar-models-evidence.md).

The OSS adapter checks collection edit authority and writes source tombstones
in the same transaction. The future Cloud adapter must obtain a validated tenant context,
check that its actor matches the caller, filter Model and Artifact reads by
organization, and check collection authority, including for worker calls.
The OSS adapter refreshes the ORM identity map when reading revision state.
PostgreSQL locks the Model row; SQLite acquires its writer
reservation before reading, because `FOR UPDATE` alone has no effect there.
Concurrent deletions therefore choose a survivor from committed, current state.
Billing, organizations, quotas and distributed execution remain Cloud
responsibilities.

Library metadata, taxonomy, favorite and batch commands live in
`library.commands`; their rollback boundary does not depend on the router.
`printing.dispatch` receives its provider builder and storage dependency and
owns immediate-send state transitions. Import Job steps and review manifests
live in `ingestion.background`; their definitions are in `ingestion.jobs`.
These are OSS operations, not additional shared-core contracts: adoption in
Cloud must preserve its durable worker and tenant policies.

Background work is declared by its owner (`<module>/jobs.py`) with the
`work.contracts` types and composed in `bootstrap.work`. Only
`runtime.engine` imports DBOS. See
[background-work.md](background-work.md) and [ADR 0008](../adr/0008-job-engine.md).

## Guarantees that interfaces must retain

A SQL transaction cannot atomically commit external bytes. Publication retains
its intent, create-only receipt, ownership registration and uncertain-commit
recovery. Never compensate an unknown commit by blindly deleting a pathname.
`artifact_content` remains the entry point for reading an Artifact's bytes.
Deletion must prove provider, namespace, key and exact object identity.

Accepted work is intent stored in the application database (a Job row, an
ingest request, a missing derivative); the engine executes it and its state is
disposable. A nudge only makes the reconciler run sooner, so losing one costs
latency, never work. Cloud's durable queue and tenant-aware worker fencing
remain separate product adapters behind the same `JobEngine` port.

Event publication and HTTP connections are separate responsibilities. Local
fan-out is best effort, and so is the PostgreSQL `NOTIFY` bus a split topology
uses; after a reconnect clients are told to resync. Cloud's transactional outbox must be written inside its
unit of work when an event must survive a crash after commit. A successful
in-process publish is not evidence of durable browser delivery.

Restore recovery retains its sidecar journal, storage receipts, database marker,
maintenance gate and drain protocol. These are part of the storage consistency
contract. The maintenance gate is a database fence (`work.fences`) with a
holder, heartbeat and TTL, so it also stops Job steps in other processes; an
interrupted restore is still governed by its filesystem journal, and a fence's
expiry never overrides it.

## Validation

Tests mirror their owning module inside `unit`, `integration` and `contract`.
Moving a seam includes moving its test and updating fault injection to the
lookup actually used by production. Retain assertions about failure outcomes,
not just successful imports. Run query budgets, OpenAPI, schema parity,
migration upgrades and publication/restore failure cases throughout extraction.
See `docs/backend-refactor-validation.md` for the behavior matrix and evidence.

Similarity analysis is an optional derivative of committed Artifacts. Each
unfinished `SimilarityRun` is the subject of `similarity.analyze` Jobs in the
`similarity` lane, under restore/cleanup admission. The engine runs one
execution per run; that execution is the run's `writer`, and every checkpoint,
candidate and vector publication is fenced on it. The run itself holds only
intent and progress: scope, settings snapshot, the checkpoint of each mesh,
shortlist or verified pair, and cancellation.
Geometry arrays and embedding contracts live in `printstash-core`; file parsers,
OCP/ONNX children, storage materialization and SQL authorization stay in the app.
See [ADR 0005](../adr/0005-similar-models-evidence.md) for source/version fencing
and the separation between measured evidence and human relationships.
