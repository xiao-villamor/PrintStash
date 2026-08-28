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
  - `inbox` + `staging_leases` — Pending Import state transitions and durable
    staging ownership; see [capture.md](capture.md).
  - `provenance` + `source_covers` — Model Source snapshots, overrides, and
    cover lifecycle; portable capture contracts live in `printstash-core`.
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
the in-repository README/docs. If the public site also needs an update, identify
the separate `printstash-landing` change without widening scope implicitly.
Compose files: `docker-compose.yml` (build),
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
      `alembic upgrade head` → app boots (`tests/integration/db/migrations/` +
      CI migration-upgrade job cover the basics).

## Testing expectations

- `cd backend && ./scripts/test.sh fast -q` — focused development loop.
- `cd backend && ./scripts/test.sh full -q` — backend handoff gate, including
  the full test layers configured by the repository scripts.
- Report actual results; never claim a run you did not do.
- Data-integrity and security fixes: write the failing test first
  (AGENTS.md rule 4).
- Lint: `uv run ruff check app/ tests/`; typecheck: `uv run pyright`.
- Formatting writes files. Run `uv run ruff format --check app/ tests/` for a
  read-only check, and `uv run ruff format app/ tests/` only for owned files.
- No real secrets/access codes in fixtures or tests.

### Test layers

The directory is the tier, and `backend/tests/` mirrors `app/`. Full conventions
live in the `create-tests` skill; the homes are:

- **Unit** — `backend/tests/unit/<app path>/test_<module>.py`. Pure logic. A
  conftest guard fails any test here that asks for `db_session`/`client` or opens
  a socket.
- **Integration** (the default) — `backend/tests/integration/<app path>/`. Real
  SQLite with the production pragmas, real routers, real storage; only egress is
  stood in for, and a socket guard enforces that.
- **Contract** — `backend/tests/contract/<app path>/`. Our clients against
  contract-enforcing fakes over a real loopback socket. Faults come from the
  fake's own flags, never from patching.
- **Backend e2e** — `backend/tests/e2e/`. Boots the real app and drives full
  flows against contract-enforcing fakes under `tests/fakes/` (printer
  emulators, `mock_oidc_provider.py`). Part of `pytest tests` since it's a
  subdirectory; no separate command. Fakes share a wall-clock `print_sim.py`
  so no real hardware or background tasks are needed.
- **Mock-API Playwright** — `frontend/tests/e2e/`, run with `pnpm test:e2e`.
  Route smoke tests against mocked API responses.
- **Real-backend Playwright** — `frontend/tests/e2e-real/`, run with
  `pnpm test:e2e:real`. Drives the UI against a real uvicorn backend.

### Printer emulators (standalone)

The HTTP-transport emulators are runnable standalone for manual testing (check
each file's docstring for its exact flags — they differ per provider):

```bash
cd backend
uv run python -m tests.fakes.mock_printer   --port 7125 --print-seconds 5   # Moonraker + Spoolman
uv run python -m tests.fakes.mock_prusalink --port 8080 --auth-mode api_key --api-key secret
uv run python -m tests.fakes.mock_octoprint --port 5000 --print-seconds 5
```

Bambu's MQTT/FTPS protocol fakes run in-process and verify credentials, TLS,
topics, pushall status, command acknowledgements, and upload semantics. Centauri
Carbon (CC1) runs real SDCP frames over a loopback WebSocket; Carbon 2 remains a
connection-seam fake because its protocol needs MQTT registration plus HTTP
serial-number bootstrap. These are test helpers rather than standalone CLIs.

### The rule

**New feature = unit tests + one e2e test for its headline capability.** The
unit tests cover the branches; the e2e test proves the release's marquee flow
works end to end through the real app (and, for UI features, through
`e2e-real/`). This applies to humans and AI contributors alike.

### CI

Per-PR jobs live in `.github/workflows/ci.yml` (backend + coverage gate, frontend,
e2e-real, security, migration-upgrade, docker). The PostgreSQL and SeaweedFS
contracts have no job of their own any more: the `backend` job's `full` lane runs
them, because `backend/tests/containers.py` starts both services itself.
A `schedule` (nightly) + `workflow_dispatch` re-runs the whole gauntlet as a
comprehensive off-peak / on-demand gate. The `backend` job now starts a real
SeaweedFS through `backend/tests/containers.py`, so `S3StorageBackend`
(`app/services/storage_backend.py`) and the S3 branches of
`app/services/backup.py` execute under the coverage gate rather than being
excluded from it.

Coverage is gated in three places, all with **branch coverage on** and all
two-sided (a floor that is cleared by more than its slack fails the run and has
to be raised — a floor nobody moves stops being a gate):

| Job | Command | Floors |
| --- | --- | --- |
| `backend` | `./scripts/test.sh coverage` | `backend/tests/repo/test_coverage_floors.py` — aggregate, `MODULE_FLOOR = 90`, and a capped pin list for the modules already below it |
| `printer-core` | `packages/printstash-core/scripts/test.sh coverage` | that package's own `tests/repo/test_coverage_floors.py` — `MODULE_FLOOR = 96`, no pins |
| `frontend` | `pnpm coverage` | `frontend/scripts/coverage-gate.mjs` — per-area floors for the app and both workspace packages |

None of these is `--cov-fail-under`: that flag can only check the aggregate, and
at 21,000 statements a 900-line service can fall from 95% to 70% and move the
total by half a percent. Two things changed the headline numbers when the system
went in, neither of them a regression — branches now count (95.07% by lines,
93.35% with branches), and the frontend include widened from `src/lib/**` to all
of `src/` (86% → 36%), which had also never run in CI at all.

The create-tests skill carries the workflow: how to get a report, how to turn a
partial branch into a matrix row, and why the report is the audit pass rather
than the source of the matrix.

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
