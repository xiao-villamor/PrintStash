# AGENTS.md — PrintStash

## Communication: CAVEMAN ALWAYS ON

ALL agent output ultra-compressed:
- Drop articles, filler, pleasantries.
- Fragments ok. 1-3 lines max unless asked for detail.
- Never preamble "Here is..." "The answer is..." "Based on...".
- Code blocks preserved exactly.
- Minimize output tokens.

## Project: PrintStash

Self-hosted 3D printing asset manager. FastAPI backend + Next.js 16 frontend.
Stack: Python 3.11+/SQLModel+Alembic | React 19/Tailwind 3 | SQLite(local)/Postgres(opt)/S3(opt).
Printers: Moonraker/Klipper (stable), Bambu LAN (beta).

## Skill: printstash

Load via skill tool: `name=printstash`. Contains:
- Full directory map
- All 14 DB models + relationships
- Router→Service→DB flow
- Design patterns (ADR-0001 ContextVar, ADR-0002 FrozenSettings, soft-delete, audit)
- Dev commands (backend, frontend, tests, lint)
- Common task recipes (add endpoint, add model, add provider, add page)
- Code conventions (backend python, frontend react/ts)

## Key Rules

- Never guess URLs or secrets.
- Follow existing code patterns exactly.
- Backend: `uv` not pip. Frontend: `pnpm` not npm.
- Services never import from `api/` or `fastapi`.
- Config: use `get_config()`, never `settings` directly.
- Frontend API: use `lib/api/<domain>.ts`, never raw fetch.
- Run lint+typecheck after code changes: `ruff check/format` (backend), `pnpm lint` (frontend).
