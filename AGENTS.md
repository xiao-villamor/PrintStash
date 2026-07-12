# PrintStash — agent guide

Self-hosted 3D print library (models, G-code revisions, printers, filament).
Local-first: SQLite + local FS default; Postgres/S3 optional. No hard deps on
Redis/queues/cloud.

## Layout
- `backend/` FastAPI + SQLModel + Alembic. App code in `backend/app/{api,core,db,services,schemas}`; tests in `backend/tests`.
- `frontend/` Vite + React + TS.
- Domain language: read `CONTEXT.md` before touching library/trash/storage code — terms there are binding (Model, Artifact, Revision, live/trashed, storage key…).
- Design + motion language: read `DESIGN.md` before adding or restyling UI — tokens, the motion scale, and the `components/ui/` primitives are binding. Compose the primitives; never hand-roll an overlay, and never type a raw duration, cubic-bezier, or `[var(--…)]` color into a component.
- Public roadmap: `docs/roadmap.md`. Local-only planning (gitignored): `reports/` — start with `reports/14-implementation-plan-to-1.0.0.md` (OSS plan) and `reports/15-cloud-implementation-plan.md` (cloud). Never commit or quote `reports/` content publicly.

## Commands
- Backend: `cd backend && uv run pytest tests -v` · lint `uv run ruff check app/ tests/` · run `uv run uvicorn app.main:app --reload` · migrate `uv run alembic upgrade head`
- Frontend: `cd frontend && pnpm dev|test|lint|typecheck`
- Full stack: `docker compose -f docker-compose.light.yml up` (prebuilt image — src edits need vite dev server).

## Hard rules
0. Git author/committer email for any commit in this repo must be `xiaovilamor@gmail.com` (the address tied to the `xiao-villamor` GitHub account). Never use the user's other email addresses (e.g. from session/system context) — GitHub attributes commits by verified email, and a mismatch shows the commit under the wrong account.
1. Never edit/delete/branch a merged Alembic migration — add a new one. Self-hosters upgrade from old releases; test upgrades with real data.
2. Version bumps are a triple: `backend/pyproject.toml` + `backend/app/core/config.py` + `frontend/package.json` (+ git tag) must match.
3. Branches are version numbers (`0.8.3`), not `fix/`/`feat/`. Semver: 0.x.y patch = fixes only.
4. One PR per bug/feature. Tests first on data-integrity/security fixes.
5. Keep cloud seams clean (StorageBackend, SessionFactory, RealtimeBus, TaskQueue): interface + local default; no external-service hard deps in core.
6. Frontend UI follows `DESIGN.md`. The zero-counts are load-bearing: no `transition-all`, no `ease-in`, no raw durations/cubic-beziers, no arbitrary `[var(--…)]` colors. Nothing animates over 300ms; route navigation never animates.

## Release
Bump the triple → commit `chore(release): bump to X.Y.Z` → PR → merge main → `git tag vX.Y.Z && git push origin vX.Y.Z` (CI publishes GHCR) → `gh release create vX.Y.Z`. Pre-tag: tests, ruff, upgrade-from-previous-release boot check, compose smoke, changelog.

GitHub release format (match existing releases, e.g. `v0.8.3`, `v0.8.1`):
title is `vX.Y.Z — short theme` (a few words, not just the version — omit only
if the release has no unifying theme); body is the `CHANGELOG.md` entry for
that version, `###` headers (Security/Fixed/Performance/Added as applicable),
a leading bold callout line only for upgrade-behavior warnings; end with
`**Full changelog:** https://github.com/xiao-villamor/PrintStash/compare/vPREV...vX.Y.Z`.
Don't prefix the body with a `##`/`###` version header — the release title
already carries the version.

### What's next
0.9.0 (Provider Maturity — PrusaLink, Elegoo, OctoPrint, provider conformance
suite) is in flight on the `0.9.0` branch; 0.8.5 (CI/ops hardening) shipped.
Next up is 0.10 — Library Workflow Polish (`docs/roadmap.md`). Check
`reports/14-implementation-plan-to-1.0.0.md` for current status before starting;
items may already be scoped or partially done.
