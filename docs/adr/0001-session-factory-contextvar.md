# ADR-0001: SessionFactory via ContextVar, not module-level singleton

**Status:** accepted

We inject database sessions into background tasks (ingestion pipeline, PrinterHub)
through a `SessionFactory` Protocol stored in a `contextvars.ContextVar`, not
through module-level imports of `db.session.engine`.

## Why

Two reasons collided:

1. **Today:** three different injection mechanisms coexist — FastAPI dependency
   injection for request handlers, a lazy `from app.db.session import engine`
   fallback in `ingestion.py`, and hardcoded `Session(engine)` in three
   `PrinterHub` methods. Tests work around this by monkeypatching two separate
   module attributes (`app.db.session.engine` and `app.services.printer_hub.engine`).
   This is fragile and forces every new background task author to guess which
   mechanism to use.

2. **Async support:** Postgres introduces `AsyncSession`. A Protocol that defines both
   `session()` and `async_session()` lets us swap adapters without changing
   call sites.

## Considered Options

- **Module-level singleton** (simpler, but breaks async isolation under Postgres
  and perpetuates the monkeypatch pattern)
- **FastAPI app.state** (works for request handlers but not for long-lived
  asyncio workers like PrinterHub that outlive a single request)
- **ContextVar** (chosen — per-task isolation, future-proof for async, forces
  explicit injection at every call site)

## Consequences

- Every background task must push a factory into the ContextVar before doing
  DB work. The FastAPI lifespan handler sets the default factory.
- Tests set a test factory into the ContextVar once per test; no more
  monkeypatching module attributes.
- The Protocol lives in `app/db/session.py`, which becomes the single home
  for all session concerns (engine creation, migration, factory, deps).
