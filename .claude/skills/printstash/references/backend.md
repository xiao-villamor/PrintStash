# Backend

FastAPI + SQLModel + Alembic under `backend/app/{api,core,db,services,schemas}`.
Domain language in `CONTEXT.md` is binding — read it before touching
library/trash/storage code.

## Architecture map

- `api/v1/` — routers. Thin: no Model→response hand-mapping (that's
  `services/model_views`), no business logic.
- `services/` — one module per concern; single-owner seams are the rule:
  - `ingestion.persist_artifact` — the ONLY artifact-persistence path
    (version → canonical move → File row → thumbnail → Metadata).
  - `model_views` — the ONLY Model→response composition.
  - `trash` — full trash lifecycle incl. hourly GC.
  - `storage_backend` / `storage` — StorageBackend seam (local + S3); callers
    use storage keys and `local_path()`, never branch on backend type.
  - `printer_provider` + per-provider modules — see
    [providers.md](providers.md).
- `db/scopes.py` — `live()` / `trashed()` predicates. Hand-written
  `deleted_at.is_(None)` is a bug.
- Cloud seams (keep clean, per AGENTS.md rule 5): StorageBackend,
  SessionFactory, RealtimeBus, TaskQueue — interface + local default, no
  external-service hard deps in core.
- Heavy mesh dependencies stay lazy-loaded (`CONTRIBUTING.md` boundary).

## Configuration

`backend/app/core/config.py` `Settings` is the source of truth for every env
var; prefix is `VAULT_` (e.g. `VAULT_DB_URL`, `VAULT_DATA_DIR`). Add new
settings there with a safe local-first default; document user-facing ones in
README/wiki. Compose files: `docker-compose.yml` (build),
`docker-compose.light.yml` (prebuilt GHCR image), `.prod`, `.test`.

## Migration checklist

Files in `backend/alembic/versions/`, named `<rev>_snake_description.py`
(e.g. `e2b6c9a4f7d3_octoprint_provider.py`). Create with
`uv run alembic revision -m "snake description"`.

- [ ] NEVER edit, delete, or branch a merged migration — add a new one
      (self-hosters upgrade from any old release).
- [ ] SQLite AND Postgres compatible (SQLite: no ALTER COLUMN — use
      `batch_alter_table`; see existing migrations for the pattern).
- [ ] Data backfills live in the migration when correctness depends on them
      (e.g. `e8d1c5b3a7f2_backfill_recommended_gcode.py`,
      `b2d8f6a1c94e_repair_orphan_fk_rows.py`).
- [ ] Test the upgrade path with real data: previous-release DB →
      `alembic upgrade head` → app boots (`tests/test_migrations.py` +
      CI migration-upgrade job cover the basics).

## Testing expectations

- `cd backend && uv run pytest tests -v` — must pass; report actual results,
  never claim a run you didn't do.
- Tests are per-concern files (`tests/test_<service>.py`); e2e flows in
  `tests/e2e/` with fakes under `tests/e2e/fakes/`.
- Data-integrity and security fixes: write the failing test first
  (AGENTS.md rule 4).
- Lint: `uv run ruff check app/ tests/` and `uv run ruff format app/ tests/`.
- No real secrets/access codes in fixtures or tests.

## API changes

- API-first: the web UI uses the same `/api/v1` API available to scripts.
- Additive changes preferred; never silently change response shapes — note
  schema/API changes in the PR template's Notes section and changelog.
- Capability-style discovery over hard errors (see provider
  `as_api_dict()` pattern) when a feature isn't uniformly available.

## Security rules

- Policy: `SECURITY.md`. Never sign tokens with the published default JWT
  secret (guarded in code + `test_jwt_secret.py`).
- Outbound fetches go through the SSRF guard (`browser_fetch`/import
  resolvers pin the validated address).
- Secrets are redacted in audit diffs (`services/audit`) and never returned
  by diagnostics endpoints — keep new fields on that path.
- Login/refresh are rate-limited; don't add unauthenticated endpoints beyond
  health/setup.
