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
0.9.0 (Provider Maturity — PrusaLink, Elegoo, OctoPrint, conformance suite) is
in flight on branch `0.9.0`: triple bumped, changelog written, not yet tagged.
Latest shipped: v0.8.5. Next: 0.10 — Library Workflow Polish
(`docs/roadmap.md`). Private plans live in gitignored `reports/` — start with
`reports/14-implementation-plan-to-1.0.0.md` (OSS) and
`reports/15-cloud-implementation-plan.md` (cloud); never commit or quote them.

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
| "What's next" / roadmap planning | `reports/14-implementation-plan-to-1.0.0.md` (read only the needed section) |

## Workflow for any change

1. Branch off `main` named as the target version (`0.9.1`), never `fix/`/`feat/`.
2. Implement the minimal change at the owning seam; data-integrity/security
   fixes get tests first.
3. Validate: `cd backend && uv run pytest tests -v && uv run ruff check app/ tests/`;
   frontend `pnpm lint && pnpm typecheck` (+ `pnpm test` if logic changed).
   Report results honestly — never say tests passed without running them.
4. Update docs the change invalidates (changelog entry, `docs/provider-support.md`,
   `docs/known-limitations.md`, wiki) — see the routing table.
5. One PR per bug/feature, conventional commit messages, repo git identity.

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
