---
title: Architecture
description: "How the pieces fit together: request flow, the ingestion pipeline, and the decisions behind them."
---

PrintStash is a FastAPI backend and a Vite/React single-page app, packaged for
Docker Compose. The design is deliberately small and one-directional: it's easy
to follow a request from the browser to the database and back, and the
boundaries are enforced rather than merely suggested.

## Stack

- **Backend:** Python 3.11+, FastAPI, SQLModel, Alembic
- **Frontend:** React 19, React Router 7, TanStack Query, Vite, Tailwind
- **Storage:** SQLite (default) or Postgres; local disk (default) or S3/R2
- **Printers:** Moonraker/Klipper (stable); Bambu LAN, PrusaLink, and Elegoo Centauri (beta)

The frontend was migrated off Next.js to a Vite SPA in 0.4. The app was already
~95% client-rendered behind auth, and under multi-user RBAC server rendering
broke outright: the server had no access to the browser-held token. The
production image now serves the static build via nginx, which proxies `/api/v1`
and WebSockets to the API on the same origin.

## Repository layout

```
backend/
  app/
    api/        # FastAPI routers: the HTTP layer, and nothing more
    services/   # business logic: never imports from api/ or fastapi
    db/         # models, session factory, migrations, query scopes
    core/       # config, cross-cutting concerns
    schemas/    # Pydantic/SQLModel read/write schemas
    main.py     # app entrypoint
frontend/
  src/
    pages/      # route entry components
    components/ # UI components
    lib/        # api clients, query hooks, helpers
docs/
  wiki/         # this site (Astro Starlight)
  adr/          # architecture decision records
```

## Request flow

The backend keeps a strict, one-way dependency chain:

```
HTTP request → Router (api/) → Service (services/) → DB (db/) → response schema
```

Routers handle HTTP concerns (parsing, status codes, auth) and then call a
service. **Services never import from `api/` or `fastapi`.** They hold the
business logic and reach the database through an *injected* session, which keeps
them unit-testable without spinning up the web layer and keeps the HTTP layer
thin. If you find yourself importing FastAPI inside a service, that's the smell
that logic is leaking into the wrong layer.

One module worth knowing by name: **model views** (`services/model_views`) is the
single owner of every Model → response-schema composition: browse list, detail,
export, trash list, vault stats. Routers never hand-map Model rows into
responses; they ask model views. That's why the same model looks consistent
across every view that shows it.

On the frontend, data flows through a typed API layer (`lib/api/<domain>.ts`)
wrapped by TanStack Query hooks (`lib/queries.ts`). Query keys mirror backend
resource roots, so a mutation can invalidate exactly the lists and details it
touched. Shared reads (collections, tags, printers, stats) revalidate on window
focus, which is how another user's edits surface in your tab without a manual
refresh.

## The ingestion pipeline

Getting a file into the vault is the most invariant-heavy operation in the app,
so it lives in exactly one place: `services/ingestion.persist_artifact`. The
sequence is fixed:

```
assign version → move into canonical storage → write the File row →
render thumbnail → extract metadata
```

Both background ingestion (web/API uploads) and revision attachment call this
same function; nothing re-implements it. That's why uploading through the UI,
the REST API, and the OrcaSlicer hook all behave identically, down to the
recommended-revision rule: the first G-code on a model claims the recommended
marker right here in the pipeline, so the
[invariant](/concepts/core-concepts/#the-recommended-revision-invariant)
can't be violated by a code path that forgot about it.

## Live printer status: the PrinterHub

Printer state doesn't go through the request/response path. A long-lived
component, the **PrinterHub**, keeps each provider's status in memory, writes a
coarse snapshot to the database, and fans live updates out to browsers over
WebSocket. The hub is what makes the live status badge and reconnect indicator
work, and it's a background task, which is exactly the kind of context the
session-injection decision below was made for.

## Shared volumes: scanning and watching

A [shared volume](/guides/shared-volumes/) is a folder PrintStash
indexes in place. One service, `services/external_library`, owns the reconcile:
`scan_library()` walks the folder, diffs it against the index, and applies
adds/updates/removes, with the safety guards that abort instead of mass-deleting
when a root is missing or unexpectedly empty. Every trigger funnels through this
one function, so they all share the same guarantees.

There are three triggers, layered so the reliable path always works and the fast
path is purely additive:

- **Scheduled scans.** A 60-second tick in the app lifespan checks each enabled
  volume's cron schedule (`croniter`) and runs the ones that are due. This is the
  baseline and works on any filesystem, including network mounts.
- **Manual scans.** The `POST /libraries/{id}/scan` endpoint queues a one-off scan
  as a background task and reports progress through the job registry.
- **Real-time watching.** A long-lived **LibraryWatcher** (wired into the lifespan
  alongside the PrinterHub) keeps one `watchfiles` watcher per eligible volume. A
  burst of filesystem events is debounced and then triggers the same
  `scan_library()`; the watcher never re-implements indexing, so the guards come
  for free.

Watching is only started where it works. `detect_fs_kind()` classifies the root
(reading `/proc/self/mountinfo`) as local, network, or unknown; under the default
`AUTO` mode only local filesystems are watched, because the kernel doesn't deliver
inotify events for NFS/SMB/CIFS. The watcher set is reconciled against the DB both
on a periodic tick and immediately after a create/update/delete, and a failed
watcher is isolated: it logs and falls back to the schedule rather than crashing
startup.

## Soft-delete scopes

Trash isn't a flag you check by hand. Live vs. trashed rows are expressed only
through `app.db.scopes.live()` and `app.db.scopes.trashed()` predicates, with no
hand-written `deleted_at IS NULL` anywhere. If a trashed model ever shows up in a
browse list, the bug is a query that forgot to apply the `live` scope, and the
fix is a one-liner. See
[Core concepts → Trash](/concepts/core-concepts/#trash-and-the-soft-delete-lifecycle).

## Design decisions (ADRs)

Architectural decisions are recorded under `docs/adr/`. Two are worth reading
here because they shape day-to-day code.

### ADR-0001: SessionFactory via ContextVar

Background tasks (the ingestion pipeline, the PrinterHub, the shared-volume scan
loop and LibraryWatcher) need a database session,
but they don't live inside a request, so they can't rely on FastAPI's dependency
injection. Rather than importing a module-level engine (a global singleton that
tests end up fighting), sessions are provided through a `SessionFactory` protocol
stored in a `contextvars.ContextVar`. Each context gets the correct session, and
tests can swap the factory cleanly.

### ADR-0002: Frozen settings + runtime overlay

The global `settings` singleton is split in two: a **frozen**, env-only
`Settings` that is never mutated after import, and a separate DB-backed
`RuntimeOverlay`. A `ConfigResolver` provides the single read path:
`effective = overlay[key] ?? frozen[key]`. Code keeps reading `settings.<key>` as
before; runtime changes go through the overlay instead of rewriting frozen
config. The payoff is that environment values are a stable, auditable source of
truth on boot, while a small set of values can still be adjusted live from the
admin UI. The environment surface is documented in
[Configuration](/getting-started/configuration/).
