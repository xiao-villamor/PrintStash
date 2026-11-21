---
name: backend-engineering
description: >
  Plan, implement, or review PrintStash FastAPI, SQLModel, storage, configuration, printer providers, or durable background work.
---

# Backend engineering

Read the [backend reference](references/backend.md) for capability ownership,
composition, configuration, API changes, and test seams. Use
[implementation-principles](../implementation-principles/SKILL.md) when deciding
where behavior belongs.

| When changing | Read |
| --- | --- |
| Persisted state, Alembic, SQLite or PostgreSQL, live/trashed queries | [Database](references/database.md) |
| Jobs, Work Sources, lanes, Derivatives, recipe versions | [Background work](references/background-work.md) |
| Printer protocols, capabilities, work status, or emulator contracts | [Providers](references/providers.md) |
| Inbound capture, Pending Imports, or provider connections | [Capture engineering](../capture-engineering/SKILL.md) |
| API requests, responses, errors, or events consumed by another layer | [Frontend-backend integration](../frontend-backend-integration/SKILL.md) |

Trace the production entry point to the capability owner. Identify the
authority, transaction boundary, and recovery behavior before adding writes.
Translate external failures at their adapters. Keep routers responsible for
HTTP and leave domain behavior with its owner.

Use [create-tests](../create-tests/SKILL.md) to prove the owner contract and
[run-tests](../run-tests/SKILL.md) for local checks and the final backend gate.
Completion evidence covers each affected consumer, persisted or externally
visible effect, and applicable compatibility path.
