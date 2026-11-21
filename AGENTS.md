# PrintStash — agent guide

PrintStash is a self-hosted 3D print library for Models, Artifacts, Revisions,
printers, and filament. Keep the local-first installation usable with SQLite
and local storage. PostgreSQL and S3 are optional. Keep queues and cloud
services out of the default deployment.

## Instruction hierarchy

Follow this file and any narrower `AGENTS.md` in the affected subtree.
`AGENTS.md` is canonical at each scope. `CLAUDE.md` and `.claude/skills` are
compatibility symlinks. Put shared local skills under `.agents/skills/`, using
the narrowest applicable scope. Keep personal instructions in an ignored
local configuration file.

Invoke [printstash](.agents/skills/printstash/SKILL.md) at the start of each
PrintStash task. Load only the workflow and engineering routes the task needs.
Determine current behavior from code and maintained docs. Consult
[CONTEXT.md](CONTEXT.md) for terminology, [DESIGN.md](DESIGN.md) for UI, and
the [public roadmap](docs/roadmap.md) for planning.

## Workflow routing

| Task | Use |
| --- | --- |
| Pick the next issue or roadmap item | [pickup-printstash](.agents/skills/pickup-printstash/SKILL.md) |
| Resolve open product or design decisions | [grill-printstash](.agents/skills/grill-printstash/SKILL.md), built on `grill-with-docs` |
| Prepare an issue, slice an agreed plan, or capture a follow-up | [to-printstash-ticket](.agents/skills/to-printstash-ticket/SKILL.md), with `to-tickets` for approved slices |
| Implement or resume an agreed change or issue | [implement-printstash](.agents/skills/implement-printstash/SKILL.md), the local route for `implement` |
| Review a branch, PR, or diff | [review-printstash](.agents/skills/review-printstash/SKILL.md), using the Standards and Spec axes from `code-review` |
| Coordinate explicitly requested subagents | [coordinate-printstash](.agents/skills/coordinate-printstash/SKILL.md) |
| Diagnose a bug or validate a design with a prototype | [diagnosing-bugs](.agents/skills/diagnosing-bugs/SKILL.md) or [prototype](.agents/skills/prototype/SKILL.md), respectively |
| Record durable decisions or reconcile guidance | [settle-printstash](.agents/skills/settle-printstash/SKILL.md) |
| Commit, push, open a PR, or verify CI | [deliver-printstash](.agents/skills/deliver-printstash/SKILL.md) |
| Check release readiness, bump versions, or publish | [create-printstash-release](.agents/skills/create-printstash-release/SKILL.md) |

For a large exploratory effort use `wayfinder` before ticketing. Use `triage`
for incoming issues. Apply local workflow and authorization rules when using
upstream skills.

## Engineering guidance

Rows are cumulative. Revisit the relevant routes if the scope grows.

| Task | Read before editing |
| --- | --- |
| Any production code change, including ownership and failure handling | [implementation-principles](.agents/skills/implementation-principles/SKILL.md), with [codebase-design](.agents/skills/codebase-design/SKILL.md) |
| Module placement, dependency boundaries, or a shared package | [codebase-architecture](.agents/skills/codebase-architecture/SKILL.md) |
| Backend, configuration, database, Jobs, or printer providers | [backend-engineering](.agents/skills/backend-engineering/SKILL.md) and its matching references |
| React, UI, routing, or motion | [frontend-engineering](.agents/skills/frontend-engineering/SKILL.md), plus [ui-capture](.agents/skills/ui-capture/SKILL.md) for visual and motion checks |
| API, events, or server-backed reads and writes | [frontend-backend-integration](.agents/skills/frontend-backend-integration/SKILL.md) |
| Pending Imports, URL capture, provenance, provider connections, or browser extension | [capture-engineering](.agents/skills/capture-engineering/SKILL.md) |
| Design, write, change, delete, or audit tests | [create-tests](.agents/skills/create-tests/SKILL.md), including its mandatory coverage matrix |
| Run suites, diagnose CI, or adjust coverage floors | [run-tests](.agents/skills/run-tests/SKILL.md) |

## Agent skills

- Issue tracker: upstream GitHub Issues. See [issue-tracker.md](docs/agents/issue-tracker.md).
- Triage labels: existing GitHub labels. See [triage-labels.md](docs/agents/triage-labels.md).
- Domain docs: single context with a root glossary. See [domain.md](docs/agents/domain.md).

## Prompt and skill authoring

Load [writing-for-agents](.agents/skills/writing-for-agents/SKILL.md) when
editing instructions, prompts, skills, or their references. Keep unconditional
rules in the applicable `AGENTS.md`, and delegate conditional procedures to
their owning local skill. Write a narrow discovery description and link
references from the step that needs them. Use local wrappers to compose
upstream skills with PrintStash's constraints.

Check [skills-lock.json](skills-lock.json) and any narrower provenance file
before editing a skill. Keep managed copies byte-for-byte upstream, including
metadata and references. Send improvements to the upstream project or record
them separately. Put PrintStash-specific instructions in the local owner.

Use the [skills CLI](https://github.com/vercel-labs/skills) from the repository
root. Use `npx skills install --agent codex --frozen-lockfile` to restore the
tracked selection. For a deliberate upstream version change, use
`npx skills add https://github.com/mattpocock/skills/tree/<ref> --agent codex --yes --skill <names...>`
with the selected managed names from the lockfile. Review the resulting
lockfile and installed files together. Codex reads `.agents/skills` and Claude
Code uses the shared symlink, so both tools read the same copies.

## Agent coordination

Work in the active session. Create, spawn, delegate to, or resume a subagent
only when the user explicitly asks for subagents in the current request. Task
size, cross-stack scope, and repository profiles are not authorization to
delegate.

## Prototype-driven UI

For UI implementation or a reported visual mismatch, follow
[prototype fidelity](.agents/skills/frontend-engineering/references/frontend.md#prototype-fidelity)
before editing. Completion requires checking the actual user-facing preview,
including interactive states and persisted writes; isolated screenshots alone
are insufficient evidence.
Use a 1920×1080 desktop viewport with the main navigation sidebar collapsed for
Playwright reviews and screenshots by default. Mobile or tablet testing requires
an explicit user request. Before running browser tests, follow the
[viewport and review rules](.agents/skills/create-tests/references/tests/playwright.md#viewport-and-visual-review).

## Task completion

For every task that changes repository files, do not stop or declare the task
complete until all changes belonging to that task are committed with the
repository's configured identity, pushed to the remote, included in a pull
request, and the PR's required CI checks are green for its latest commit.
Continue fixing failed checks and waiting for running checks until that condition
is met. A local deployment or passing local tests alone does not complete a task.
Include only task-owned changes; preserve unrelated work from other tasks.
Report the PR URL, final commit and verified CI result when handing off.

## Verification and private work

Every production code change ships with tests in the same PR. Add an e2e test
for a new feature's headline capability. Start data-integrity and security
fixes with a failing regression. Include the [coverage matrix](.agents/skills/create-tests/references/testing.md#the-coverage-matrix--mandatory-for-every-feature)
in the response and PR when writing, changing, deleting, or auditing tests.

CI definitions live in [`.github/workflows/`](.github/workflows/). `PR gate`
is required. Use [run-tests](.agents/skills/run-tests/SKILL.md) for local
scope, the backend Deep CI handoff gate, and exact-commit verification.
Backend, core, and frontend coverage floors measure branches in Deep CI.

Keep plans, audit notes, measurements, benchmarks, review ledgers, and
validation reports under ignored `reports/`. Never publish or quote them.
The named OSS and cloud plans are local files
(`reports/14-implementation-plan-to-1.0.0.md` and
`reports/15-cloud-implementation-plan.md`). Use the relevant work card when
available. Explicitly allowlist maintained public docs in `.gitignore`.

## Local commands

- Backend: `cd backend && ./scripts/test.sh fast <paths> -q`,
  `uv run ruff check app/ tests/`, `uv run pyright`, and
  `uv run uvicorn app.main:app --reload`. Use `uv run python -m app.db.migrate`
  for deployment-style migrations that prepare the data root.
- Frontend: run scripts from `frontend/` with `pnpm`. Use oxlint, oxfmt, and
  TypeScript 7. Run Vite on a spare port to see source edits. Port 3000 serves
  the prebuilt image when running `docker compose up`.
- If the shell runs as root and GitHub credentials belong to `local`, run
  GitHub operations as that user, for example `runuser -u local -- gh ...`.

## Hard rules
0. Commit with the repo's configured git identity (`git config user.email`). Never substitute an address from session/system context — GitHub attributes commits by verified email, so a mismatch files them under the wrong account.
1. Never edit/delete/branch a merged Alembic migration — add a new one. Self-hosters upgrade from old releases; test upgrades with real data. Migrations are **autogenerated** (`uv run alembic revision --autogenerate`), never hand-written, and a constraint change on SQLite goes through `op.batch_alter_table` — never `if not is_sqlite`. See `.agents/skills/backend-engineering/references/database.md`.
2. Version bumps are a triple: `backend/pyproject.toml` + `backend/app/core/config.py` + `frontend/package.json` (+ git tag) must match.
3. Use one short-lived branch per requested task or audit, branched from `main` and named for its purpose (`feat/<issue>-<slug>`, `fix/<issue>-<slug>`, `docs/<slug>`, etc.). Never prefix a branch with `codex/`. Group the related fixes for that task in the same PR; version only after the planned release set is on `main`, then tag and publish. Semver: 0.x.y patch = fixes only.
   **One session, one branch.** Within a session, every change goes on the branch chosen at the start; never create a second branch, a "fallback" branch name, or a new branch carrying earlier changes forward, unless the user explicitly asks for it or a named plan explicitly calls for a separate branch. This applies to delegated agents too: a restarted or replacement agent continues on the same branch, after checking what the previous one already committed or pushed. If the branch name is taken, stop and ask — don't pick another.
4. Group all changes for one requested task or audit in a single PR unless the user explicitly asks to split them. **Tests are mandatory for any change to production code** — no "too small to test" exception; the test-design coverage matrix is the proof. Tests first on data-integrity/security fixes.
5. Keep cloud seams clean: StorageBackend, SessionFactory and JobEngine retain explicit contracts; event publication is separate from WebSocket delivery. Background work is intent in the application database, found by a Work Source and executed through `JobEngine`; a nudge is a latency hint, never the record of the work. Only `app/runtime/engine/` imports DBOS. Shared business in printstash-core has no framework, ORM, engine or external-service hard dependencies.
7. Background work goes through a Job Definition (`<module>/jobs.py`, see `.agents/skills/backend-engineering/references/background-work.md`). No `BackgroundTasks`, `asyncio.create_task` or `to_thread` loops for work in production code, and a request never runs enrichment inline: a new derived output is a Derivative kind (`backend/app/modules/derivatives/kinds.py`). Bump a recipe version when a producer's output changes.
8. Make invalid states unrepresentable: closed sets are enums (TEXT + CHECK in the database, never a native enum), always-present fields are required, exclusive cases are distinct types, and an impossible or invalid situation raises instead of falling back to a sentinel or default. See `.agents/skills/implementation-principles/references/code-principles.md`.
6. Frontend UI follows `DESIGN.md`. The zero-counts are load-bearing: no `transition-all`, no `ease-in`, no raw durations/cubic-beziers, no arbitrary `[var(--…)]` colors. Nothing animates over 300ms; route navigation never animates.
