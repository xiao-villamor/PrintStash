# PrintStash — agent guide

Self-hosted 3D print library (models, G-code revisions, printers, filament).
Local-first: SQLite + local FS default; Postgres/S3 optional. No hard deps on
Redis/queues/cloud.

Workflow reference (release procedure, roadmap position, plan pointers):
`.claude/skills/printstash/SKILL.md` — read it before release or roadmap
work; that detail lives there, not here, so this file stays small.

## Bounded coordination

For every task that authorizes code changes, use the repository-scoped
`printstash_coordinator` under `.codex/agents/`. The coordinator delegates code
to these workers:

- Backend, API, database, migrations, storage, services, providers, and backend
  tests → `printstash_backend_implementer`.
- Frontend, shared web-domain packages, UI, browser extension, and their tests →
  `printstash_web_implementer`.

For a cross-stack change, dispatch both workers with explicit, non-overlapping
file ownership. Settle the shared contract and its canonical owner before they
work in parallel; the backend worker owns backend/core contracts and the web
worker consumes them. The coordinator owns synthesis, conflict avoidance, and
one final integration gate. It follows a finite loop:

1. Set acceptance checks and ownership before dispatch.
2. Run one implementation pass with the fewest useful workers.
3. Run one consolidated review that reports every blocking finding in one batch.
4. Run at most one correction pass, limited to that batch, then verify once and
   hand off.

After the correction pass, newly discovered critical security or data-loss risk
is reported as a blocker; all other findings become follow-up work. A second
correction or review cycle requires explicit user approval. Do not recursively
review reviewers, repeat unchanged gates, or widen acceptance criteria mid-run.

All code-building workers use `gpt-5.6-luna` with `xhigh` reasoning and Fast
service. Workers do not spawn agents. Read-only explanation, planning, release,
and documentation-only tasks stay with the coordinator. If a named worker
profile is unavailable, use a generic worker with the same Luna/xhigh settings;
the coordinator does not take over code implementation.

## Layout
- `backend/` FastAPI + SQLModel + Alembic. App code in `backend/app/{api,core,db,services,schemas}`; tests in `backend/tests`.
- `frontend/` Vite + React + TS.
- Domain language: read `CONTEXT.md` before touching library/trash/storage code — terms there are binding (Model, Artifact, Revision, live/trashed, storage key…).
- Design + motion language: read `DESIGN.md` before adding or restyling UI — tokens, the motion scale, and the `components/ui/` primitives are binding. Compose the primitives; never hand-roll an overlay, and never type a raw duration, cubic-bezier, or `[var(--…)]` color into a component.
- Public roadmap: `docs/roadmap.md`. Local-only planning (gitignored): `reports/` — start with `reports/14-implementation-plan-to-1.0.0.md` (OSS plan) and `reports/15-cloud-implementation-plan.md` (cloud). Never commit or quote `reports/` content publicly.

## Commands
- Backend: fast loop `cd backend && ./scripts/test.sh fast -q` · full gate `./scripts/test.sh full -q` · lint `uv run ruff check app/ tests/` · run `uv run uvicorn app.main:app --reload` · migrate `uv run alembic upgrade head`
- Frontend: `cd frontend && pnpm dev|test|lint|format|typecheck` — oxlint + oxfmt + TypeScript 7 (no ESLint, no prettier)
- Full stack: `docker compose -f docker-compose.light.yml up` (prebuilt image — src edits need vite dev server).

## Testing
Backend tiers are directory-owned: `backend/tests/{unit,integration,contract,e2e}/`;
shared protocol fakes live in `backend/tests/fakes/`. The canonical tier policy,
mirrored layout, and lane definitions live in `.claude/skills/create-tests/`.
Mock-API Playwright lives in `frontend/tests/e2e/` (`pnpm test:e2e`) and
real-backend Playwright in `frontend/tests/e2e-real/` (`pnpm test:e2e:real`).
Printer emulators run standalone for manual testing, e.g.
`cd backend && uv run python -m tests.fakes.mock_printer --port 7125 --print-seconds 5`
(see `references/backend.md` for per-provider flags). **Rule: every change
to production code ships with tests in the same PR — features, fixes,
refactors, config, migrations alike. A new feature adds one e2e test for its
headline capability on top.** CI runs everything per-PR plus a
nightly/`workflow_dispatch` full-matrix re-run.
Any test-related work — writing, changing, deleting, or auditing tests, or
deciding what a change needs — starts by loading
`.claude/skills/create-tests/SKILL.md` (the `create-tests` skill). Its
coverage matrix (one row per behaviour, every row `✅`/`❌`/`⏭️`) is mandatory
in the response and the PR; a change without a matrix is not done.

## Hard rules
0. Commit with the repo's configured git identity (`git config user.email`). Never substitute an address from session/system context — GitHub attributes commits by verified email, so a mismatch files them under the wrong account.
1. Never edit/delete/branch a merged Alembic migration — add a new one. Self-hosters upgrade from old releases; test upgrades with real data.
2. Version bumps are a triple: `backend/pyproject.toml` + `backend/app/core/config.py` + `frontend/package.json` (+ git tag) must match.
3. Use one short-lived branch per change, branched from `main` and named for its purpose (`feat/<issue>-<slug>`, `fix/<issue>-<slug>`, `docs/<slug>`, etc.). Merge features independently; version only after the planned release set is on `main`, then tag and publish. Semver: 0.x.y patch = fixes only.
4. One PR per bug/feature. **Tests are mandatory for any change to production code** — no "too small to test" exception; the `create-tests` coverage matrix is the proof. Tests first on data-integrity/security fixes.
5. Keep cloud seams clean (StorageBackend, SessionFactory, RealtimeBus, TaskQueue): interface + local default; no external-service hard deps in core.
6. Frontend UI follows `DESIGN.md`. The zero-counts are load-bearing: no `transition-all`, no `ease-in`, no raw durations/cubic-beziers, no arbitrary `[var(--…)]` colors. Nothing animates over 300ms; route navigation never animates.

## Release & roadmap
Follow `.claude/skills/printstash/SKILL.md` — read it before cutting a
release, bumping versions, or picking the next roadmap item. Don't
reconstruct the procedure from memory or git history.
