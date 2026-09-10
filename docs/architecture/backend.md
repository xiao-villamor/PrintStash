# Backend module boundaries

PrintStash is a modular monolith. FastAPI is the HTTP adapter, SQLModel and
Alembic own persistence, and the default deployment remains one process with
SQLite and local storage. PostgreSQL, S3 and OpenDAL are optional adapters.
Moving a responsibility does not change its database schema, storage keys,
HTTP endpoint or persisted archive format.

## Ownership

| Owner | Responsibilities | Public operations and contracts |
| --- | --- | --- |
| `library` | Models, Artifacts, G-code Revisions, taxonomy, multipart sets, provenance and trash | `commands`, `revisions`, `model_views.{listing,pagination,detail,facets,exports,statistics,trash}`, `multipart_models`, `part_options`, `provenance`, `source_covers`, `taxonomy`, `saved_views`, `trash` |
| `ingestion` | Accepting bytes, staging, Inbox review, URL imports and portable transfer | `ingestion.persist_artifact`, `background`, `inbox`, `importer`, `library_transfer`, `staging_leases`, `staging_cleanup` |
| `sources` | Mounted/remote libraries, discovery, root enrollment and watching | `contracts`, `root_binding`, `library_source`, `external_library`, `library_watcher`, `library_locations` |
| `storage` | Object identity, ownership, publication, reading and verified deletion | `storage_backend.contracts`, `artifact_content`, `storage_ownership`, `storage_deletion`, `storage_operations`, `storage_connections`, `storage_paths`, `storage_providers` |
| `backups` | Snapshot creation, catalogue, verification, replicas and journaled restore | `backup.creation`, `backup.catalogue`, `backup.verification`, `backup.adoption`, `backup.deletion`, `backup.restore`, `backup.recovery`, `backup_runs`, `retry_commands`, `backup_schedule` |
| `printing` | Printers, provider adapters, fleet scheduling, materials and print history | `dispatch`, `costing`, `printer_provider`, `printer_hub`, `fleet`, `materials`, `printer_files`, `printer_jobs`, `print_results`, `multipart_builds` |
| `similarity` | Versioned geometric evidence, indexed retrieval, durable analysis runs and explicit review | `fingerprints`, `retrieval`, `processing`, `candidates`, `review`, `composition` |
| `inference` | Local native embedding contracts, immutable index generations and authorized semantic queries | `local`, `manifest`, `store`, `search` |
| `media` | Mesh processing, thumbnails, source covers and toolpaths | `mesh_operations`, `thumbnail_engine`, `thumbnail_generations`, `thumbnail_repair`, `toolpath`, `source_cover_processing` |
| `identity` | Product identity, collection/printer authorization, sharing and tickets | `auth`, `oidc`, `rbac`, `printer_rbac`, `share`, `ws_tickets` |
| `notifications` | Notification preparation and delivery | `notifications`, `notification_renderers` |
| `administration` | Dynamic OSS settings, setup, audit and operational inspection | `runtime_config`, `setup_bootstrap`, `audit`, `vault_audit`, `release_check` |
| `runtime` | Local process coordination, maintenance, job tracking and delivery transports | `maintenance`, `jobs`, `work_wakeup`, `realtime` |
| `db` | Session factories, SQL schema and metadata registration | `session`, `scopes`, `models`, `publication`, `transactions` |

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
`api.session_cookie` and `api.setup_session`; installation locking remains in
`administration.setup_bootstrap`.
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
owns immediate-send state transitions. Import job handlers and review manifests
live in `ingestion.background`, with the existing local lifecycle. These are
OSS operations, not additional shared-core contracts: adoption in Cloud must
preserve its durable worker and tenant policies.

## Guarantees that interfaces must retain

A SQL transaction cannot atomically commit external bytes. Publication retains
its intent, create-only receipt, ownership registration and uncertain-commit
recovery. Never compensate an unknown commit by blindly deleting a pathname.
`artifact_content` remains the entry point for reading an Artifact's bytes.
Deletion must prove provider, namespace, key and exact object identity.

OSS task transport is a process-local wake-up hint. Accepted work is stored in
the database; this transport supplies neither durable delivery nor leases.
Cloud's durable queue and worker fencing are separate product adapters, not an
implementation of the same enqueue/dequeue contract.

Event publication and HTTP connections are separate responsibilities. Local
fan-out is best effort. Cloud's transactional outbox must be written inside its
unit of work when an event must survive a crash after commit. A successful
in-process publish is not evidence of durable browser delivery.

Restore recovery retains its sidecar journal, storage receipts, database marker,
maintenance gate and drain protocol. These are part of the storage consistency
contract. The OSS in-memory maintenance gate is valid only in the supported
single-process deployment; it is not a distributed lock.

## Validation

Tests mirror their owning module inside `unit`, `integration` and `contract`.
Moving a seam includes moving its test and updating fault injection to the
lookup actually used by production. Retain assertions about failure outcomes,
not just successful imports. Run query budgets, OpenAPI, schema parity,
migration upgrades and publication/restore failure cases throughout extraction.
See `docs/backend-refactor-validation.md` for the behavior matrix and evidence.

Similarity analysis is an optional derivative of committed Artifacts. The runtime
coordinates wakeups, restore/cleanup admission and the shared thumbnail compute
budget; the capability owner checkpoints each mesh, shortlist or verified pair.
Geometry arrays and embedding contracts live in `printstash-core`; file parsers,
OCP/ONNX children, storage materialization and SQL authorization stay in the app.
See [ADR 0005](../adr/0005-similar-models-evidence.md) for source/version fencing
and the separation between measured evidence and human relationships.
