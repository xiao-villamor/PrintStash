---
title: Architecture
description: Stack, repository layout, request flow, and key design decisions.
---

PrintStash is a FastAPI backend and a Vite/React single-page app, packaged for
Docker Compose.

## Stack

- **Backend:** Python 3.11+, FastAPI, SQLModel, Alembic
- **Frontend:** React 19, React Router 7, TanStack Query, Vite, Tailwind
- **Storage:** SQLite (default) or Postgres; local disk (default) or S3/R2
- **Printers:** Moonraker/Klipper (stable), Bambu LAN (beta)

## Repository layout

```
backend/
  app/
    api/        # FastAPI routers (HTTP layer)
    services/   # business logic — never imports from api/ or fastapi
    db/         # models, session factory, migrations
    core/       # config, cross-cutting concerns
    schemas/    # Pydantic/SQLModel read/write schemas
    main.py     # app entrypoint
frontend/
  src/
    pages/      # route entry components
    components/ # UI components
    lib/        # api clients, query hooks, helpers
docs/
  wiki/         # this documentation site (Starlight)
```

## Request flow

The backend keeps a strict one-directional dependency:

```
HTTP request → Router (api/) → Service (services/) → DB (db/) → response schema
```

Routers handle HTTP concerns and call services. **Services never import from
`api/` or `fastapi`** — they hold business logic and talk to the database
through an injected session. This keeps services unit-testable and the HTTP
layer thin.

On the frontend, data flows through a typed API layer (`lib/api/<domain>.ts`)
wrapped by TanStack Query hooks (`lib/queries.ts`). Query keys mirror backend
resource roots so a mutation can invalidate exactly the lists and details it
affects, and shared reads (collections, tags, printers, stats) revalidate on
window focus.

## Design decisions (ADRs)

Architectural decisions are recorded under `docs/adr/`.

### ADR-0001 — SessionFactory via ContextVar

Database sessions are injected into background tasks (the ingestion pipeline,
PrinterHub) through a `SessionFactory` protocol stored in a
`contextvars.ContextVar`, rather than via module-level imports of the engine.
This lets background work get a correct session per context and keeps tests from
fighting a global singleton.

### ADR-0002 — Frozen settings + runtime overlay

The global `settings` singleton is split into a **frozen**, env-only `Settings`
(never mutated after import) and a separate DB-backed `RuntimeOverlay`. A
`ConfigResolver` provides the single read-path: `effective = overlay[key] ??
frozen[key]`. Code keeps reading `settings.<key>`; mutations go through the
overlay instead of rewriting frozen config. See
[Configuration](/PrintStash/getting-started/configuration/) for the
environment-variable surface.
