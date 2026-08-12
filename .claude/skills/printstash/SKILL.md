---
name: printstash
description: Project skill for working on PrintStash — task routing, workflow, conventions, release procedure, provider architecture, and roadmap position. Invoke at the start of ANY PrintStash task (CLAUDE.md requires it), then read only the reference file for the task at hand — releases, migrations, providers, UI, or planning.
---

# PrintStash

Self-hosted 3D print library (Models, G-code Revisions, printers, filament).
Local-first: SQLite + local FS default; Postgres/S3 optional; no hard deps on
Redis/queues/cloud. `AGENTS.md` (layout, commands, hard rules) is binding.

## Where we are

<!-- Update this block when a release ships. -->
Latest shipped: v0.11.3 (Centauri Carbon upload beta, spool safeguards,
ingestion fixes, and data-integrity hardening), merged to `main` and tagged.
Active release work: `0.11.4`, on the existing version branch and consolidated
PR. It combines the large-library performance pass with every backend-audit
finding marked **Implementar ahora** or **Planificar**, by explicit release
direction. Read `reports/17-backend-audit-0.11.4-implementation-plan.md` for
the immediate findings and
`reports/18-backend-audit-planned-findings-0.11.4.md` for the planned findings;
read
`reports/16-large-library-performance-implementation-plan.md` for the scale
work already on the branch. Keep each fix in a traceable commit, obey the
plans' internal gates, and run the real PostgreSQL and incremental Pyright
gates introduced by the planned-finding pass. After 0.11.4 ships, resume 0.12
planning from `docs/roadmap.md`.

Private plans live in `reports/`. They are local-only: never commit, publish,
or quote them. Older long-range plans may not exist in every checkout; when
`reports/14-implementation-plan-to-1.0.0.md` or
`reports/15-cloud-implementation-plan.md` is absent, use the public roadmap
and changelog instead of reconstructing their contents.

## Before changing anything

1. Read the canonical doc for the domain you're touching (binding language):
   - Library / trash / storage code → `CONTEXT.md`
   - Any UI work → `DESIGN.md` (tokens, motion scale, `components/ui/` primitives)
2. Trace the real flow in code before editing — e.g. artifact writes go through
   `services/ingestion.persist_artifact`, Model→response mapping through
   `services/model_views`, live/trashed queries through `app.db.scopes`.
   Single-owner seams like these are the norm; don't re-implement one.
3. Feature claims: check `docs/provider-support.md` (stable/beta levels),
   `docs/known-limitations.md`, and `docs/roadmap.md` before stating something
   is supported. Roadmap ≠ shipped.

## Task routing

| Task | Read |
| --- | --- |
| Branch, commit, PR, changelog | [references/conventions.md](references/conventions.md) |
| Cut / publish a release, version bump | [references/release.md](references/release.md) |
| Backend, DB migration, testing, config | [references/backend.md](references/backend.md) |
| Frontend / UI change | [references/frontend.md](references/frontend.md) |
| Printer providers (new or changed) | [references/providers.md](references/providers.md) |
| Implement a backend-audit finding on `0.11.4` | `reports/17-backend-audit-0.11.4-implementation-plan.md` for **Implementar ahora**; `reports/18-backend-audit-planned-findings-0.11.4.md` for **Planificar** (read shared constraints, dependency graph, finding card, and gate) |
| Continue the `0.11.4` large-library pass | `reports/16-large-library-performance-implementation-plan.md` (read only the relevant card and shared constraints) |
| "What's next" / roadmap planning after `0.11.4` | `reports/14-implementation-plan-to-1.0.0.md` when present (read only the needed section); otherwise `docs/roadmap.md` + `CHANGELOG.md` |

## Workflow for any change

1. Branch off `main` named as the target version (`0.9.1`), never `fix/`/`feat/`.
2. Implement the minimal change at the owning seam; data-integrity/security
   fixes get tests first.
3. Validate: `cd backend && uv run pytest tests -v && uv run ruff check app/ tests/`;
   frontend `pnpm lint && pnpm typecheck` (+ `pnpm test` if logic changed).
   Report results honestly — never say tests passed without running them.
   Backend validation also includes `uv run pyright`; PostgreSQL-affecting
   changes run `PRINTSTASH_TEST_POSTGRES_URL=... uv run pytest tests/postgres -v`
   against a real supported server.
4. Update docs the change invalidates (changelog entry, `docs/provider-support.md`,
   `docs/known-limitations.md`, docs — now in the `printstash-landing` repo,
   not this one) — see the routing table.
5. Normally use one PR per bug/feature, conventional commit messages, and the
   repo git identity. The existing `0.11.4` consolidated PR is the explicit
   exception: keep its audit fixes traceable and respect plans 17 and 18; do
   not split them onto another branch unless the user reverses that release
   direction.

## Common mistakes to avoid

- Claiming beta/roadmap features are supported (provider support levels are
  explicit in `docs/provider-support.md`).
- Hand-rolling UI: raw durations, `[var(--…)]` colors, custom overlays — the
  zero-counts in `DESIGN.md` are load-bearing.
- Editing/deleting a merged Alembic migration (self-hosters upgrade from old
  releases) — always add a new one.
- Writing `deleted_at.is_(None)` by hand instead of `scopes.live()`.
- Secrets, printer access codes, or real API keys in code, fixtures, tests,
  logs, or issue text.
- Committing gitignored material (`reports/`, `docs/internal/`) or generated
  files; bumping versions outside a release commit.
