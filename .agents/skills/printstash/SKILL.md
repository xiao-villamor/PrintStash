---
name: printstash
description: Work on PrintStash implementation, testing, releases, providers, capture, UI, or roadmap decisions. Invoke at the start of every PrintStash task, then follow the routing table to load only the references the task needs.
---

# PrintStash

Self-hosted 3D print library (Models, G-code Revisions, printers, filament).
Local-first: SQLite + local FS default; Postgres/S3 optional; no hard deps on
Redis/queues/cloud. `AGENTS.md` (layout, commands, hard rules) is binding.

## Where we are

<!-- Update this block when a release ships. -->
Latest shipped: v0.12.1 (container startup compatibility for legacy or
operator-supplied uv commands), merged to `main` and tagged. Next: gather
upgrade and hardware feedback. `CHANGELOG.md` `Unreleased` is the canonical
summary of work not yet shipped; a branch name or roadmap entry is not a release.

Private plans live in `reports/`. They are local-only: never commit, publish,
or quote them. Older long-range plans may not exist in every checkout; when
`reports/14-implementation-plan-to-1.0.0.md` or
`reports/15-cloud-implementation-plan.md` is absent, use the public roadmap
and changelog instead of reconstructing their contents.

## Before changing anything

1. Inspect the current branch and `git status`. Preserve user and concurrent
   edits. If the requested change is already in progress, continue in its
   existing branch and ownership boundary; create a branch only for a genuinely
   new standalone change.
2. Read the canonical doc for the domain you're touching (binding language):
   - Library / trash / storage code → `CONTEXT.md`
   - Any UI work → `DESIGN.md` (tokens, motion scale, `components/ui/` primitives)
3. Trace the real flow in code before editing — e.g. artifact writes go through
   `services/ingestion.persist_artifact`, Model→response mapping through
   `services/model_views`, live/trashed queries through `app.db.scopes`.
   Single-owner seams like these are the norm; don't re-implement one.
4. Feature claims: check `docs/provider-support.md` (stable/beta levels),
   `docs/known-limitations.md`, and `docs/roadmap.md` before stating something
   is supported. Roadmap ≠ shipped.

## Task routing

| Task | Read |
| --- | --- |
| Branch, commit, PR, changelog | [references/conventions.md](references/conventions.md) |
| Cut / publish a release, version bump | [references/release.md](references/release.md) |
| Backend, config | [references/backend.md](references/backend.md) |
| Schema change, migration, soft-delete query | `.claude/skills/database/SKILL.md` — autogenerate only; SQLite constraint work needs `op.batch_alter_table` |
| Write, change, or audit tests (any layer) | `.agents/skills/create-tests/SKILL.md` — coverage matrix mandatory; then its per-runtime reference |
| Run the suites, chase a failure, move a coverage floor | `.agents/skills/run-tests/SKILL.md` |
| Frontend / UI change | [references/frontend.md](references/frontend.md) |
| Pending Imports, URL capture, provenance, provider connections, or browser extension | [references/capture.md](references/capture.md) plus backend/frontend reference(s) for the layers changed |
| Printer providers (new or changed) | [references/providers.md](references/providers.md) |
| Implement work from a named private plan | The named file in `reports/`; read only its shared constraints and the relevant work card |
| "What's next" / roadmap planning | `reports/14-implementation-plan-to-1.0.0.md` when present (needed section only), otherwise `docs/roadmap.md` + `CHANGELOG.md` |

## Workflow for any change

1. For a new standalone change, branch from an up-to-date `main` and name the
   branch for its purpose (`feat/<issue>-<slug>`, `fix/<issue>-<slug>`,
   `docs/<slug>`, etc.). In an existing task branch or shared worktree, stay on
   that branch and keep to the assigned files.
2. Implement at the owning seam. Data-integrity/security fixes get a red test
   first. Every new feature gets focused unit/integration coverage and one e2e
   test for its headline capability.
3. Run focused checks while iterating, then the applicable gate:
   - Backend: `cd backend && ./scripts/test.sh fast -q`; before handoff of a
     backend change, `./scripts/test.sh full -q`,
     `uv run ruff check app/ tests/`, and `uv run pyright` when feasible.
   - Frontend: `cd frontend && pnpm format:check && pnpm lint && pnpm typecheck`;
     add `pnpm test` for logic and the applicable Playwright suite for a
     headline UI flow.
   - Browser extension: use [references/capture.md](references/capture.md).
   PostgreSQL-affecting changes also run the supported-server contract suite.
   Report only checks actually run and preserve failure output.
4. Update the changelog and repository docs the change invalidates. Public site
   docs that live in `printstash-landing` are a separate repository change;
   identify it without editing another repository unless that scope was assigned.
5. When the task includes commit or PR preparation, use one PR per bug/feature,
   conventional commits, and the repository's configured git identity. Do not
   reuse a historical release branch as precedent for combining unrelated work.
6. When explicitly asked to cut a release, first confirm that each completed PR
   is independently merged to `main` and CI is green, then follow
   [references/release.md](references/release.md). Never collect feature work on
   a version-number branch.

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
- Treating a dirty/shared worktree as disposable, overwriting concurrent edits,
  or silently widening an assigned file set.
- Committing gitignored material (`reports/`, `docs/internal/`) or generated
  files; bumping versions outside a release commit.
