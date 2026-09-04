# PrintStash — agent guide

Self-hosted 3D print library (models, G-code revisions, printers, filament).
Local-first: SQLite + local FS default; Postgres/S3 optional. No hard deps on
Redis/queues/cloud.

Workflow reference (release procedure, roadmap position, plan pointers):
`.agents/skills/printstash/SKILL.md` — read it before release or roadmap
work; that detail lives there, not here, so this file stays small.

## Skill

`.agents/skills/printstash/` is the repository's only public skill;
`.claude/skills` is a symlink to `.agents/skills`, and this file is what
`CLAUDE.md` points at. Invoke **`printstash`** at the start of every task. Its
routing table discloses the relevant database, test-design, test-running,
frontend, backend, provider, capture, release, and workflow references.

## Agent coordination

Work in the active session. Create, spawn, delegate to, or resume a subagent
only when the user explicitly asks for subagents in the current request. Task
size, cross-stack scope, and repository profiles are not authorization to
delegate.

## Layout
- `backend/` FastAPI + SQLModel + Alembic. App code in `backend/app/{api,core,db,services,schemas}`; tests in `backend/tests`.
- `frontend/` Vite + React + TS.
- Domain language: read `CONTEXT.md` before touching library/trash/storage code — terms there are binding (Model, Artifact, Revision, live/trashed, storage key…).
- Design + motion language: read `DESIGN.md` before adding or restyling UI — tokens, the motion scale, and the `components/ui/` primitives are binding. Compose the primitives; never hand-roll an overlay, and never type a raw duration, cubic-bezier, or `[var(--…)]` color into a component.
- Public roadmap: `docs/roadmap.md`. Local-only planning (gitignored): `reports/` — start with `reports/14-implementation-plan-to-1.0.0.md` (OSS plan) and `reports/15-cloud-implementation-plan.md` (cloud). Never commit or quote `reports/` content publicly.

## Commands
- Backend: fast loop `cd backend && ./scripts/test.sh fast -q` · full gate `./scripts/test.sh full -q` · coverage gate `./scripts/test.sh coverage` · lint `uv run ruff check app/ tests/` · run `uv run uvicorn app.main:app --reload` · migrate `uv run alembic upgrade head`
- Frontend: `cd frontend && pnpm dev|test|coverage|lint|format|typecheck` — oxlint + oxfmt + TypeScript 7 (no ESLint, no prettier)
- Full stack: `docker compose -f docker-compose.light.yml up` (prebuilt image — src edits need vite dev server).
- Local dev gotcha: `:3000` serves the **prebuilt** image, not HMR. Run the vite
  dev server on a spare port to see `frontend/src` edits at all.

## Testing
**The directory a test lives in is its tier**, and `backend/tests/` mirrors `app/`:
`unit/` (pure logic, no DB and no socket — both enforced by a guard), `integration/`
(the default: real SQLite, real routers, egress stood in for), `contract/` (our clients
against contract-enforcing fakes over a real loopback socket), `e2e/` (the whole app
over ASGITransport), plus `fakes/`, `fixtures/` and `repo/` (repo-level invariants).
So `app/services/trash.py` ↔ `tests/integration/services/test_trash.py`, and "is this
module tested?" is one `ls`. Lanes: `./scripts/test.sh fast|contract|e2e|full|coverage`
(`--help` explains each). Then mock-API Playwright (`frontend/tests/e2e/`,
`pnpm test:e2e`) and real-backend Playwright (`frontend/tests/e2e-real/`,
`pnpm test:e2e:real`).
Printer emulators run standalone for manual testing, e.g.
`cd backend && uv run python -m tests.fakes.mock_printer --port 7125 --print-seconds 5`
(see `.agents/skills/printstash/references/backend.md` for per-provider flags). **Rule: every change
to production code ships with tests in the same PR — features, fixes,
refactors, config, migrations alike. A new feature adds one e2e test for its
headline capability on top.** CI runs everything per-PR plus a
nightly/`workflow_dispatch` full-matrix re-run.
Any test-related work — writing, changing, deleting, or auditing tests, or
deciding what a change needs — starts by loading
`.agents/skills/printstash/references/testing.md`. Its
coverage matrix (one row per behaviour, every row `✅`/`❌`/`⏭️`) is mandatory
in the response and the PR; a change without a matrix is not done. Running the
suites and closing what they report — a red gate, a floor to raise, a flake, a
merge that left stubs on renamed seams — follows
`.agents/skills/printstash/references/running-tests.md`.

Coverage is gated with **branches on** in all three suites, and every floor is
two-sided — clear it by more than its slack and the run fails until the floor is
raised, so a PR that improves coverage sometimes edits a floor, and that edit is
the point. `./scripts/test.sh coverage` (backend, floors in
`tests/repo/test_coverage_floors.py`: aggregate + a floor every module clears +
a capped debt list), the same lane inside `packages/printstash-core`, and
`pnpm coverage` (frontend, floors in `frontend/scripts/coverage-gate.mjs`). The
report is the **audit** pass: it finds matrix rows you forgot, and it can never
supply them — it is produced by running the implementation, and it measures what
executed rather than what was asserted. Playwright is invisible to all of it.

## Hard rules
0. Commit with the repo's configured git identity (`git config user.email`). Never substitute an address from session/system context — GitHub attributes commits by verified email, so a mismatch files them under the wrong account.
1. Never edit/delete/branch a merged Alembic migration — add a new one. Self-hosters upgrade from old releases; test upgrades with real data. Migrations are **autogenerated** (`uv run alembic revision --autogenerate`), never hand-written, and a constraint change on SQLite goes through `op.batch_alter_table` — never `if not is_sqlite`. See `.agents/skills/printstash/references/database.md`.
2. Version bumps are a triple: `backend/pyproject.toml` + `backend/app/core/config.py` + `frontend/package.json` (+ git tag) must match.
3. Use one short-lived branch per change, branched from `main` and named for its purpose (`feat/<issue>-<slug>`, `fix/<issue>-<slug>`, `docs/<slug>`, etc.). Merge features independently; version only after the planned release set is on `main`, then tag and publish. Semver: 0.x.y patch = fixes only.
4. One PR per bug/feature. **Tests are mandatory for any change to production code** — no "too small to test" exception; the test-design coverage matrix is the proof. Tests first on data-integrity/security fixes.
5. Keep cloud seams clean (StorageBackend, SessionFactory, RealtimeBus, TaskQueue): interface + local default; no external-service hard deps in core.
6. Frontend UI follows `DESIGN.md`. The zero-counts are load-bearing: no `transition-all`, no `ease-in`, no raw durations/cubic-beziers, no arbitrary `[var(--…)]` colors. Nothing animates over 300ms; route navigation never animates.

## Release & roadmap
Follow `.agents/skills/printstash/SKILL.md` — read it before cutting a
release, bumping versions, or picking the next roadmap item. Don't
reconstruct the procedure from memory or git history.
