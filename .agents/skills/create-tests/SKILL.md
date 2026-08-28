---
name: create-tests
description: Use when touching any test — creating a test file, adding a case to an existing file, editing or deleting a test, auditing whether a module is covered, or choosing which tier (unit / integration / contract / e2e / Playwright) a scenario belongs to. Carries the mandatory coverage matrix, the tier policy, the mirrored test layout, file anatomy and parametrization rules, the fixture/factory system every arrange step uses, and the per-runtime conventions for pytest, vitest, and Playwright in this repo. Also applies when a production entity changes, since its factory changes with it. Not for running the suites or closing a coverage gate — that is `run-tests`.
---

# Create Tests

Any time you touch a test — a new file, a new case, an edited assertion, a
deletion — this skill applies. A one-line assertion change counts; there is no
"small tweak" carve-out.

Read this file in full, then the one reference for the runtime you're writing in:

| Writing in | Read |
| --- | --- |
| `backend/tests/**` (pytest) or `backend/packages/printstash-core/tests/**` | [references/backend.md](references/backend.md) |
| `frontend/src/**/__tests__/**` or `frontend/packages/*/src/__tests__/**` (vitest) | [references/frontend.md](references/frontend.md) |
| `frontend/tests/e2e-real/**` or `frontend/tests/e2e/**` (Playwright) | [references/playwright.md](references/playwright.md) |

Plus [references/fixtures.md](references/fixtures.md) whenever you write an
**arrange step**, add or change a **builder**, or change a **production entity** —
an entity change is incomplete until its factory matches, in the same PR.

**The arrange step is not freestyle.** Every row a backend test needs comes from
a `make_*` fixture backed by `tests/factories/`. Assembling a row inline, or
adding a module-level `_build_thing()` helper beside your tests, is the single
most common way this suite has gone wrong: the same helper reappeared 347 times,
`_user` thirteen of them, disagreeing about whether its default was a superuser.
Worse, a hand-built row that encodes state wrongly (`deleted_at` unset, a
provider's credentials missing, a manifest that matches no source) **inserts
cleanly and is then invisible to the code under test** — the test passes while
asserting against a path it never reached. `references/fixtures.md` has the rules;
`tests/factories/__init__.py` has the inventory.

## How much to test

Two independent questions decide a feature's tests:

1. **How much** — coverage completeness. *This section.*
2. **Which tier** — unit vs. integration vs. contract vs. e2e. *Tier Policy, below.*

Pick the *tier* per scenario; pick the *count* by covering every scenario. The
two don't trade off: **exhaustive** means every distinct behaviour has a test;
**not too many** means no redundant tests for the same behaviour and no
assertions on implementation details. One test per behaviour satisfies both.

### Default to exhaustive coverage

"Add a couple of tests" is not the bar. For every production change cover
**every happy path, every edge case, and every error path.** Sweep these
classes for each feature so none are silently skipped:

- **Boundaries** — min/max, off-by-one, size limits (`body_limit`, mesh
  limits, pagination `limit`/`offset`)
- **Empty / null / missing** — required field absent, optional omitted, empty
  collection, model with no files
- **Duplicates / idempotency** — same bytes uploaded twice (content-hash
  dedupe), delete-twice, replayed job, unique-constraint collision
- **Ordering / concurrency** — worker-thread writes racing reads, out-of-order
  provider events, GC running mid-operation
- **Auth / permission** — unauthenticated, wrong scope (`read`/`write`/`admin`),
  non-superuser, collection/printer RBAC role too low, share-link visibility
- **Live vs trashed** — every read over Models/Files respects
  `scopes.live()`; trashed rows are invisible, restorable, and GC'd on schedule
- **Malformed input** — wrong type, oversized, unparseable G-code/3MF, bad URL
  (SSRF guard), hostile filename
- **Downstream failure** — printer/provider times out, rejects a command,
  returns a malformed payload; notification target 5xx
- **Partial failure / rollback** — multi-step write fails midway (file row
  without metadata, thumbnail failing after persist); atomicity holds
- **Storage backend** — behaviour is identical through `StorageBackend`
  (local default; S3 branch when the change touches storage keys)
- **SQLite and PostgreSQL** — dialect-sensitive SQL (`postgres` marker) when
  the change adds a query, index, or migration

The fixtures make the Nth test nearly free — `db_session`, `client`,
`auth_headers`, the provider emulators, and `_patch_engine` truncating every
table between tests exist precisely so coverage is cheap. There is no budget to
ration.

### The coverage matrix — mandatory for every feature

Before writing tests for **any** feature, bug fix, or module — not only complex
ones — enumerate the behaviours as a coverage matrix. The matrix is how you
prove the exhaustive bar is met instead of asserting it.

**Rules:**

- **One row per observable behaviour**, which is also one test function / one
  `it` (see "One behaviour per test"). If a row's name needs the word "and",
  split it into two rows. This is enforced, not advisory: no test name in either
  Python tree may contain `_and_` (`backend/tests/repo/test_test_hygiene.py`).
  Two shapes trip it, and the fix differs — a name *joining* two behaviours is
  two tests; a name *listing* the assertions of one behaviour just needs to name
  the behaviour. Where the "and" belongs to a production symbol
  (`_download_and_collect`), the `class Test…` group carries the unit's name and
  the test names the behaviour.
- **Derive rows from requirements, never from the implementation.** Reading
  the source and matrixing what it happens to do reproduces its bugs as
  "expected." Requirements live in the issue, `CONTEXT.md`,
  `docs/provider-support.md`, and the PR summary.
- **No blank cells.** Every row has a Status; a behaviour with no test is
  `❌ missing`, not omitted.

**Standard format** (a Markdown table — use these exact columns):

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | persists the file row with its metadata in one commit | Happy | staged STL, well-formed meta | `File` + `Metadata` rows exist; returned row has id | Integration | ✅ `integration/services/ingestion/test_ingestion_atomicity.py::TestPersistArtifact::test_persists_a_file_row_with_its_metadata_in_one_commit` |
| 2 | dedupes a re-upload by content hash | Edge | same bytes uploaded twice | job state `duplicate`; one `Model` row | E2E | ✅ `e2e/test_ingest.py::test_gcode_upload_dedups_by_content_hash` |
| 3 | accepts a model name at the length limit | Edge | name = MAX chars | 201; row persisted untruncated | Integration | ❌ missing |
| 4 | hides a trashed model from the list | Edge | model with `deleted_at` set | `GET /models` omits it | Integration | ❌ missing |
| 5 | denies a `read`-scope token | Error | token scope `read` | 403; no row written | Integration | ❌ missing |
| 6 | returns all-None metadata for a file with no comments | Edge | G-code with no `;` lines | every metadata field is `None`; no exception | Unit | ✅ `gcode/test_parser.py::TestParse::test_no_comments_returns_all_none` |
| 7 | surfaces a provider upload rejection | Error | emulator started with `reject_commands=True` | `ProviderError`; job state `failed` | Contract | ❌ missing |

**What each column holds:**

- **Behaviour** — one observable behaviour phrased as the test name.
- **Category** — `Happy` / `Edge` / `Error`. Scanning this column shows at a
  glance whether all three were swept.
- **Precondition / input** — the arrange step: state and input driving this
  behaviour. For a parametrized test, the list of cases.
- **Observable outcome asserted** — the *real* artifact you assert on: a DB
  row, an HTTP status + body, a returned value, a file on the storage backend,
  a request the emulator's `Recorder` received. The boundary is the
  observable, not a spy on internal calls. Never "method X was called." If you
  can't name an observable outcome, the row isn't a behaviour — drop or
  rewrite it.
- **Tier** — `Unit` / `Integration` / `Contract` / `E2E` / `Frontend unit` /
  `Playwright`, chosen via the Tier Policy below. Don't re-justify the choice
  in the cell; the policy is the source of truth.
- **Status** — exactly one of `✅ <tier dir>/<file>::<test>` (covered),
  `❌ missing` (planned, not written), or `⏭️ N/A — <reason>` (deliberately
  not tested, reason inline).

**Surface the matrix** in your response and in the PR description (the PR
template has a section for it). An empty or `❌` cell is a visible missing
test, not a judgment call left to the reader.

### Close the loop: assess after every session

A test-writing session makes **two passes** over the matrix:

1. **Plan (before code).** Build the matrix from requirements with every
   Status `❌ missing`. This is your test plan, and it is the "tests first"
   step AGENTS.md rule 4 requires on data-integrity and security fixes.
2. **Assess (after writing).** Walk **every** row again and set its Status to
   `✅ <test>` or `⏭️ N/A — <reason>`. **Done = zero unexplained `❌`.**
   Re-print the completed matrix.

The closing assessment is mandatory. It converts "I added some tests" into
"every behaviour is covered or explicitly waived." Skipping it forfeits the
guarantee the matrix exists to provide.

### Assessing an existing suite

To answer "are the tests already here enough?" (an audit, not fresh
authoring): build the same matrix from requirements, then populate Status by
reading the **current** suite — `✅` where a test already covers the row,
`❌ missing` where none does. The `❌` rows are the coverage gap; report them
as the deliverable.

### Coverage report: the audit pass, never the source

The coverage report is the second half of the assessment pass, and its role is
narrow and easy to get backwards.

**The report cannot tell you what to test.** It is produced *by running the
implementation*, so a matrix built from it is a matrix derived from the code —
the exact thing the matrix rules forbid, because code that does the wrong thing
gets its wrong behaviour recorded as "expected." A requirement the code never
implemented has no line to be uncovered, so it appears in no report at any
percentage. **100% coverage of the wrong behaviour is still 100%.**

**It also cannot tell you a behaviour is tested.** Coverage measures what
*executed*, not what was *asserted*. A test that calls a function and asserts
nothing covers every line in it. So `✅` in the Status column never comes from
the report; it comes from a named test that asserts an observable outcome.

What the report *is* good for — and it is genuinely good for this — is finding
rows you forgot. An uncovered line or a half-taken branch is proof that some
input never reached it, and that input is almost always a row missing from the
matrix. So the loop is:

1. Build the matrix from requirements. Write the tests. (`Plan`, then write.)
2. Run coverage. Read the uncovered lines and **partial branches** in the module
   you changed.
3. For each one, ask *what input would reach this?* — and put **that** in the
   matrix as a new row, phrased as a behaviour, with a Category and a Tier.
4. Write the test for the row. Never write a test "for the line."
5. Re-run. Repeat until every gap in your module is either a row with a test or
   a deliberate `⏭️ N/A — <reason>`.

Step 3 is the whole discipline. "Line 412 is uncovered" is not a matrix row;
"rejects an archive whose manifest names a file it does not contain" is, and it
happens to cover line 412.

**Partial branches are where the real gaps are.** Branch coverage is on in every
suite, so a guard clause whose false path never runs is reported as half-covered
rather than covered. The backend reads 95.07% by lines and 93.35% once branches
count, and that ~1.7pp is 612 branches — 612 conditions that only ever went one
way in the entire suite. Those are the highest-yield rows in the report: an
`if` with one untried side is usually an error path with no test.

Commands (each writes term-missing, an HTML report, and the JSON the gate reads):

| Suite | Command | Report |
| --- | --- | --- |
| Backend | `cd backend && ./scripts/test.sh coverage` | `backend/.coverage-html/index.html` |
| One backend module's own contribution | `./scripts/test.sh coverage tests/integration/services/test_trash.py` | same |
| `printstash-core` | `cd backend/packages/printstash-core && ./scripts/test.sh coverage` | `.coverage-html/index.html` |
| Frontend (app + both workspace packages) | `cd frontend && pnpm coverage` | `frontend/coverage/index.html` |

Reading the terminal output: `1234, 1240-1243` are uncovered statements;
`146->136` and `142->exit` are **partial branches** — the arrow names the jump
that never happened, so `142->exit` means the loop at line 142 never finished
without breaking. The HTML report colours partial branches distinctly and is the
faster way in when a module has many.

### The floors are two-sided, so they move

Every suite gates on a floor that may not fall *and* may not be left behind:
exceed it by more than the slack and the run fails, telling you to raise it. A
floor nobody is ever forced to move stops being a gate — it becomes a number
everything clears by twenty points. So a PR that improves coverage sometimes has
to edit the floor, and that edit is the point, not an annoyance.

There is also a floor **per module** (backend, `printstash-core`) or **per area**
(frontend), because the aggregate is blunt: at 21,000 statements a 900-line
service can fall from 95% to 70% and move the total by half a percent. Backend
modules that do not clear 90% are pinned individually in
`backend/tests/repo/test_coverage_floors.py`, and that list is capped so it can
only shrink. If your change touches a pinned module, clearing its debt is in
scope; adding a new pin is not.

Where the numbers live:

- `backend/tests/repo/test_coverage_floors.py` — aggregate, `MODULE_FLOOR`, the pin list
- `backend/packages/printstash-core/tests/repo/test_coverage_floors.py` — same shape, higher floor, no pins
- `frontend/scripts/coverage-gate.mjs` — per-area floors for the app and both workspace packages

### What coverage cannot see

Do not read a low frontend number as "untested". `pnpm coverage` measures the
vitest run only, and the route-level behaviour of the app is covered by
Playwright — `frontend/tests/e2e/` against a mock API, `frontend/tests/e2e-real/`
against a live backend — none of which appears in any coverage report. That is
why `src/pages/**` sits at ~43% and why its floor is a record of what vitest
reaches rather than a claim about what is tested. The corollary matters more:
**adding a Playwright test moves no coverage number**, so on a UI feature the
matrix is the only evidence, and coverage is not even a cross-check.

## Never make the test environment easier than production

A test that passes because the harness is more permissive than the real system is
worse than no test: it reports safety it has not checked. This is not a
hypothetical failure mode here — it happened, at the largest possible scale.

`PRAGMA foreign_keys` was **off for the entire suite**. The between-test wipe issued
`PRAGMA foreign_keys=OFF` and `=ON` inside a transaction, and SQLite silently ignores
that pragma while one is open, so the `OFF` did nothing the author intended and the
`ON` restored nothing. From the first test of every session onward, the suite could
not fail on a foreign-key violation, and nothing asserted otherwise. What that hid:

- `DELETE /api/v1/libraries/{id}` returned **500 on a real installation** and passed
  in CI. `purge_library_index` trashed the indexed files and left
  `files.external_library_id` pointing at the library it then deleted.
- `hard_delete_file` cleaned up three of the five tables with a NOT NULL foreign key
  to `files`, so purging a file that belonged to a print batch or carried a material
  requirement failed.
- `PATCH /api/v1/libraries/{id}` accepted a `target_collection_id` that does not
  exist.
- Tests passed *for the wrong reason*: `test_enqueue_respects_printer_scope` asserted
  `0` then `1` for the **same** printer id, which only worked because neither id was
  a real row.

So the rule, and it is about design rather than diligence:

**When a test fails because production is strict, fix the code or fix the fixture —
never loosen the environment.** Reach for the factories (`tests/factories/`) so the
rows a test references actually exist; that is what they are for. A hardcoded
`owner_user_id=7` is not a shortcut, it is a row that cannot exist.

**A deliberate difference is a named, asserted difference.** Where the harness
genuinely cannot match production — `journal_mode` is `memory` because an in-memory
database cannot use WAL — say so where it is configured, and pin the rest.
`backend/tests/repo/test_db_parity.py` asserts every pragma that *can* match does,
and asserts the *behaviour* too (a dangling foreign key is refused), because a
pragma reading `1` on one connection proves nothing about the connection the test
runs on.

**Suspending a constraint for teardown is fine; suspending it for a test body is
not.** The wipe still turns foreign keys off — that is the ordinary shape of a bulk
flush, and the `files` <-> `models` cycle means no delete order exists to get right
instead. What matters is that enforcement is live for every test body.

The general shape recurs beyond the database: **a test that configures process-wide
state has to put it back.** The engine, session factory, storage backend and rate
limiters all have autouse fixtures in `tests/conftest.py` for exactly this reason.
`SQLModel.metadata` was the one nobody had noticed was in that category, and it cost
a two-in-five flake that looked like it had no cause — see
`tests/integration/postgres/conftest.py`.

### New external infrastructure ships with its test infrastructure

Not optional, and not "mock it for now". When a change makes the product talk to a
new external system — another storage provider, a queue, Redis, a search index, a
metrics sink, a third-party API — the same change adds the way that system is tested
for real:

1. **A real instance, started by the suite.** Add it to
   `backend/tests/containers.py`, next to PostgreSQL and SeaweedFS: image pinned by
   digest, its own readiness check, started lazily on the first test that needs it
   and stopped once at session end. Never a `docker run` in a README or a CI-only
   `services:` block — those are a second definition that drifts, which is exactly
   what `tests/repo/test_service_images.py` exists to prevent.
2. **A resource marker and a tier directory**, so the subset is selectable and the
   lanes stay meaningful (`postgres`, `s3`, and so on — see the tier table).
3. **Contract tests against the real thing**, in `tests/contract/`, for the
   behaviours the wire protocol actually decides: conditional writes, version ids,
   auth failures, partial reads, whatever the provider's own semantics are. A fake
   you wrote cannot disagree with your assumptions; a real service can, and that
   disagreement is the entire value.
4. **Fixtures and factories** for its rows and its credentials, so the arrange step
   of the tenth test is as cheap as the first.
5. **No Docker is an error, not a skip.** A run that selected those tests and could
   not start the service did not verify what it was asked to verify — see
   `require_docker` in `tests/containers.py`.

The reason this is a hard requirement rather than a preference: a mocked integration
tests your belief about the provider, and the interesting failures are precisely
where that belief is wrong. SeaweedFS is in this suite because its S3 gateway
changed its conditional-write and version-id behaviour between releases, and
`contract/services/test_storage_backend.py` is what would notice. A mock would have
kept passing.

A seam that must stay swappable (`StorageBackend`, `SessionFactory`, `RealtimeBus`,
`TaskQueue`) still gets its local default tested everywhere — that is what keeps the
fast lane fast. The container is for the provider-specific half, not a replacement
for the seam.

### Never skip. A skip is not a pass

A skipped test reports success having verified nothing, and a suite that counts skips
as green gives exactly the confidence it has not earned. This suite has **zero**, and
`tests/repo/test_test_hygiene.py::TestSkips` keeps it there — `pytest.skip`,
`skipif`, `xfail` and `mark.skip` all fail the hygiene check.

It had 16, and every one of them was one of two things wearing a skip:

**A missing resource — make it an error.** The run was asked to verify something and
could not; that is a failure, not an absence. `tests/containers.py` starts PostgreSQL
and SeaweedFS itself and calls `pytest.exit` with a message naming the prerequisite
when Docker is not reachable, rather than skipping 26 tests and printing green. The
same reasoning retired four more:

| Was skipped because | Now |
| --- | --- |
| `/proc/self/status` is Linux-only, so the memory-leak assertions skipped on macOS | `psutil`, which reports RSS on both — a memory test nobody runs locally is a memory test that regresses locally |
| `PRINTSTASH_MESH_CORPUS` unset | defaults to `testdata/`, which is committed; the variable still overrides it with a bigger corpus |
| a committed fixture might not exist | `tests.paths.require_fixtures()` raises at import, naming the file. These live in the repo: absence is a broken checkout, not an environment |
| `aiosqlite` might not be installed | it is a declared `dev` extra, so absence is a broken environment — the module now refuses to import |

**A case that does not apply — do not generate it.** Filter the parametrize list.
`test_transport_errors_become_provider_errors` is parametrized over the providers that
*have* an injectable transport rather than over all of them and skipping the rest, and
the factory rule over `_factory_checked_modules()` rather than every module. Generating
a case and skipping it reports a test that was never written as a test that was
declined, and that number can never become a pass.

The one legitimate exception is a **lane**, not a skip: a marker that selects a
subset, deselected by name (`postgres`, `s3`, `slow`, `coverage_gate`). Every one of
those tests runs somewhere, and the lane table says where.

If you cannot make a test pass, it is a `❌` row in the matrix — visible, in the PR —
not a skip in the suite.

## Tier Policy

**"Write tests. Not too many. Mostly integration."** The highest
confidence-per-test comes from tests that wire real components together.
Agents drift toward mocked unit tests because they're easier to generate; this
section exists to counteract that.

In this repo the real database is *free*: every test that takes `db_session`
or `client` runs against a real SQLite engine with the production pragmas
(`foreign_keys=ON`), real routers, real services, and a table wipe between
tests. So the default tier is **integration**, and the only things you ever
stand in for are egress boundaries.

### The tiers

| Tier | Where | What is real | What is stood in for | Lane |
| --- | --- | --- | --- | --- |
| **Unit** | `backend/tests/unit/<app path>/`, `printstash-core/tests/<pkg path>/` | the function | nothing | `fast` |
| **Integration** *(default)* | `backend/tests/integration/<app path>/` | SQLite, routers, services, storage backend, RBAC; real fixture files | outbound HTTP (`get_http_client`), provider transports | `fast` (minus `slow`/`postgres`/`s3`-marked subsets) |
| **Contract** | `backend/tests/contract/<app path>/` | our client against a contract-enforcing fake over a real loopback socket (emulators, Bambu MQTT/FTPS, OIDC) | nothing | `contract` |
| **E2E** | `backend/tests/e2e/` | the whole app via `httpx.ASGITransport` + fakes | nothing (`is_public_ip` relaxed for loopback) | `e2e` |
| **Frontend unit** | `frontend/src/<path>/__tests__/<module>.test.ts(x)` | component/hook + real collaborators (query hooks, api client, router, auth context) | `fetch` via `vi.stubGlobal`, or `QueryApiProvider` stubs | `pnpm test` |
| **Playwright real** | `frontend/tests/e2e-real/<feature>.spec.ts` | browser + Vite + real uvicorn + throwaway SQLite (+ mock printer) | nothing | `pnpm test:e2e:real` |
| **Playwright mock-API** | `frontend/tests/e2e/` | browser + Vite | the API (`mock-api.ts`) | `pnpm test:e2e` |

A directory is a tier; **resource markers** (`postgres`, `s3`, `slow`) gate
subsets *within* a tier. `postgres` and `s3` need a real service, and
`tests/containers.py` starts it as a container — the only path, with no
environment variable and no `docker run`. **No Docker is an error, not a skip**,
because a green run with those 21 tests absent verified neither the
dialect-sensitive SQL nor the upgrade path. Read the endpoint through
`postgres_url()` / `s3_endpoint()` inside a fixture, never as a module-level
constant. There is no "integration" marker — the
directory says it.

### Category → tier: the default mapping

The matrix's Category column decides the tier before you think about
convenience:

| Category | Default tier | Because |
| --- | --- | --- |
| **Happy** | **Integration** (Contract when the behaviour *is* the wire protocol; E2E once, for the headline flow) | the happy path must be proven through the real DB, real router, real storage backend — a mocked happy path proves the mock |
| **Edge** | **Integration** | boundaries, empties, duplicates, live/trashed visibility and RBAC only mean something against real rows and real constraints |
| **Error — contract** (401/403/404/409/422, `IntegrityError`, trashed row invisible, quota exceeded) | **Integration** | the app itself raises these; no fault injection needed |
| **Error — dependency misbehaving on cue** (timeout, malformed provider JSON, raise-once-then-succeed, disk full mid-write) | **Unit**, or **Integration with only the egress patched** | the behaviour is our reaction to the dependency's *outcome*; real infra can't produce it deterministically |
| **Error — real fake fault** (wrong access code, rejected command, flaky webhook, `PrintSim` → `ERROR`) | **Contract** via the fake's fault flag | the fake can produce it for real, so the wire-level reaction is testable without patching |

"It was easier to mock" is never a reason to move a Happy or Edge row down a
tier. "It's hard to reproduce for real" is the *only* reason an Error row
moves to a patched test — and then only the fault is patched, not the DB.

### When integration (or deeper) tests are MANDATORY

- **Every router endpoint** — the full request→response cycle through
  `TestClient`: auth scope, RBAC, validation, response shape, DB side effect.
  Only egress is mocked.
- **Every service that writes the DB** — `ingestion.persist_artifact`,
  `trash`, `library_transfer`, `printer_jobs`, `backup`: transactions,
  multi-table writes, cascades, constraint violations. These surface only
  against a real engine.
- **Every query over a soft-deletable table** — a test that a trashed row is
  invisible through the read path (`scopes.live()`), and visible through
  `scopes.trashed()`.
- **Every migration** — the upgrade-path test; a `postgres`-marked case when
  the SQL is dialect-sensitive.
- **Every provider change** — the shared conformance pack picks it up
  automatically; normalisation goes in `integration/services/test_<provider>.py`,
  wire-level behaviour in `contract/services/test_<provider>.py` against its
  emulator.
- **Every new feature** — AGENTS.md rule: *tests + one e2e test for its
  headline capability* (backend `tests/e2e/`; for UI features also
  `frontend/tests/e2e-real/`).

### When unit tests are the right choice

- **Pure logic** — parsers (`gcode_parser`, `bgcode`), hashing, URL safety,
  slug/taxonomy helpers, `model_views` mapping given built rows, frontend
  `lib/` formatters.
- **Faults hard to reproduce for real** — network timeout, malformed provider
  JSON, rate limit, a dependency raising on cue. Patch `get_http_client` (or
  inject a fake client/factory) and assert the reaction.
- **Complex branching** — state machines (`PrintSim`, job state transitions),
  many input combinations.
- **Frontend components and hooks** — rendering, interaction, store behaviour.

### Decision matrix

| What you're testing | Tier | Stand-in strategy |
| --- | --- | --- |
| Router endpoint | **Integration** | `client` + `auth_headers`; mock egress only |
| Service with DB writes | **Integration** | `db_session`; real storage backend |
| Live/trashed visibility, RBAC resolution | **Integration** | real rows, real roles |
| Real slicer output | **Integration** | fixture under `tests/fixtures/`; `slow` marker if it's a large file |
| Dialect-sensitive SQL, migration | **Integration** + `postgres` marker | real `postgres:16`, started as a container for the run |
| S3 storage paths | **Integration** + `s3` marker | real SeaweedFS, started as a container for the run |
| Provider wire protocol | **Contract** | emulator over loopback (`start_server`, `PrintSim`, `Recorder`) |
| Headline flow of a feature | **E2E** | `api` + `fakes` fixtures in `tests/e2e/` |
| Pure function | Unit | none |
| Reaction to a dependency failing | Unit / Integration | `patch("<module>.get_http_client")`, injected factory |
| React component / hook | Frontend unit | `vi.stubGlobal("fetch")`, `QueryApiProvider`, seeded `QueryClient` |
| UI flow with persistence | Playwright real | none |
| Route renders without console errors | Playwright mock-API | `mock-api.ts` |

### Never mock inside a contract or e2e test — induce only real faults

A contract or e2e test exercises real wiring end to end. The moment you
`patch`, `monkeypatch`, or override a seam *inside* one to force a failure, it
stops being a contract test on that path — it's a unit test in an integration
costume, and it proves nothing about the real system.

The fault you want decides the tier:

- **Reachable against the real fake, deterministically → contract test, real
  fault.** The emulators take fault flags for exactly this:
  `reject_commands=True`, a wrong `expected_access_code`, `PrintSim` driven
  to `ERROR`, the `/flaky/{key}` webhook target, `--auth-mode` on PrusaLink.
  Add a flag to the fake when the fault you need isn't there yet.
- **A dependency misbehaving on cue (raises once, returns garbage, times out
  N times then succeeds) → unit or integration test, patched egress.** "How
  does our code react when the client raises?" is logic over the dependency's
  *outcome*, not a property of real infra.

The smell test: **if making the contract test fail requires replacing part of
the real system with a fake, that assertion belongs in an integration test.**
The e2e conftest's single monkeypatch (`is_public_ip` for loopback) is the
ceiling, not a precedent.

## Where tests live: mirror the production tree

A test file is found by translating the production path, never by guessing a
topic name. One production module ↔ one test module, same basename, under the
tier directory:

| Production | Test |
| --- | --- |
| `backend/app/services/ingestion.py` | `backend/tests/integration/services/test_ingestion.py` (and `unit/services/test_ingestion.py` only if it has pure helpers worth isolating) |
| `backend/app/api/v1/printers.py` | `backend/tests/integration/api/v1/test_printers.py` — or, when one file would exceed ~600 lines, the folder `integration/api/v1/printers/` with `test_create.py`, `test_rbac.py`, … split by endpoint/method group |
| `backend/app/services/moonraker.py` (wire level, emulator) | `backend/tests/contract/services/test_moonraker.py` |
| `backend/app/db/migrate.py` + `alembic/` | `backend/tests/integration/db/test_migrations.py` |
| `backend/packages/printstash-core/src/printstash_core/gcode/parser.py` | `backend/packages/printstash-core/tests/gcode/test_parser.py` |
| `frontend/src/lib/auth-store.ts` | `frontend/src/lib/__tests__/auth-store.test.ts` |
| `frontend/src/components/printers-list.tsx` | `frontend/src/components/__tests__/printers-list.test.tsx` |
| `frontend/src/lib/api/models.ts` (too big for one file) | the folder `frontend/src/lib/api/__tests__/models/` with `browse.test.ts`, `batch.test.ts`, … split by endpoint group |
| `frontend/packages/domain/src/format.ts` | `frontend/packages/domain/src/__tests__/format.test.ts` — in the package that **defines** it; the app's `src/lib/format.ts` re-export shim is not a second home |
| a UI feature area (route/page) | `frontend/tests/e2e-real/<feature>.spec.ts` |

Everything that is not a mirror has one home: `backend/tests/fixtures/` (data
files), `backend/tests/fakes/` (emulators and contract fakes, shared by
`contract/` and `e2e/`), `backend/tests/repo/` and `frontend/tests/repo/`
(repo-level invariants: OpenAPI snapshot, CI config, import boundaries, the
suite's own shape, translation coverage), `backend/tests/e2e/` (flows,
`test_<flow>.py`). Every backend test directory is a package (`__init__.py`) so
`integration/services/test_auth.py` and `e2e/test_auth.py` coexist.

The mirror is load-bearing for the matrix: "does `app/services/trash.py` have
tests?" is answered by one `ls`, and an audit of a module is an audit of one
file. A test that can't be placed by this rule is testing something that
isn't a unit — find the unit first.

**A topic name is the failure mode this prevents.** `send-to-queue.test.tsx`,
`small-clients.test.ts`, `inbox-navigation.test.tsx` each defended a real module,
and none of them could be found from it. That is what turns a coverage matrix
into guesswork: nobody can tell an untested module from one whose tests live
under a name they did not think of. Both suites enforce the mirror mechanically —
`backend/tests/repo/test_test_hygiene.py` and
`frontend/tests/repo/suite-hygiene.test.ts`.

## Inside a test file

Every test file, in every runtime, has the same anatomy, top to bottom:

1. **Contract header** — module docstring (pytest) or leading block comment
   (vitest/Playwright) stating in prose what this file defends and why it
   matters when it goes red. Not a restatement of the filename.
2. **Imports**, then **module constants** — absolute instants, round numbers,
   fake credentials that are obviously fake.
3. **Local fixtures** — only environment setup this file alone needs, and only
   what two or more of its tests share. Rows come from the `make_*` fixtures, so
   a module-local row builder should not exist at all; single-use setup stays
   inline in its test, where the reader can see it next to the assertion.
   Anything three files share moves to the nearest `conftest.py`, and anything
   that builds a database row belongs in `tests/factories/` instead — see
   [references/fixtures.md](references/fixtures.md).
4. **One group per production unit, in the production module's order** —
   `class Test<Function|Endpoint|Method>` in pytest, `describe("<unit>")` in
   vitest. Never an ad-hoc group (`TestMisc`, `describe("extra cases")`, or a
   bare type name like `TestFile` when the unit is a function); a new aspect of
   a unit is a sibling test in that unit's group. **No test sits at module
   level** — `tests/repo/test_test_hygiene.py` enforces it, because a test that
   belongs to no group accumulates in write order and drifts away from the code
   it defends. The group name is what turns "what covers `scan_library`?" from a
   grep into a lookup.
5. **Inside a group, tests in matrix order: Happy → Edge → Error.** Reading
   the file top to bottom reads the matrix.

Each test body is **Arrange / Act / Assert** separated by blank lines, with no
section comments. Rules that keep it that way:

- **No conditionals or loops in a test body.** A loop is a parametrized test;
  a branch is two tests.
- **Assertions carry context**: `assert r.status_code == 201, r.text`;
  `expect(rows).toEqual([...])` over `toHaveLength` when the content matters.
- **The name is the matrix row**: `test_<verb phrase>` / `it("<verb
  phrase>")`. No `test_1`, no `test_works`, no `it("should work")`.
- **No `skip`, `skipif` or `xfail` at all** — see "Never skip" above. A missing
  resource is an error; a case that does not apply is not generated. A test you
  cannot make pass is a `❌` row in the matrix, visible, not a dead block, and not a
  skip.

### Parametrized tests

Parametrize when — and only when — **one behaviour** is exercised across
**several inputs** and the **assertion has the same shape for every case**:

- boundary sweeps (`0`, `1`, `MAX`, `MAX + 1`)
- format/dialect variants (Orca / Prusa / Cura / Bambu headers; SQLite / Postgres)
- every member of a production registry (`PROVIDERS`, `PrinterRole`,
  `FileType`, every locale) — **derive the list from the registry**, never
  copy it, so a new member is covered the day it's added (this is the
  conformance-pack pattern)

Don't parametrize when the cases assert *different things*, when the body
would need `if case.kind:` to pick an assertion, when one case needs a
materially different arrange, or when there is only one case. A parameter
list where one entry means "no error expected" and the others mean "raises X"
is two tests wearing one decorator — split it.

In the matrix, a parametrized test is **one row** (list the cases in the
Precondition column) as long as the assertion is identical; a case with its
own assertion is its own row and its own test. Every case gets a readable
id (`ids=` / `pytest.param(id=...)`; vitest `$label`) so a failure names the
variant, not `[3]`.

Runtime mechanics are in the references.

### One behaviour per test

Each test asserts on **one observable behaviour**, and its name says exactly
which. A name needing "and" is the tell, and it is guarded absolutely — see the
matrix rules above.

```python
# ❌ two behaviours in one test
def test_create_printer_returns_201_and_persists_row(): ...

# ✅ one behaviour per test
def test_returns_the_created_printer(): ...
def test_persists_a_row(): ...
def test_rejects_read_scope(): ...
```

Splitting one of these is not cosmetic. Doing it across this suite repeatedly
turned up assertions that had been passing for the wrong reason, because the
first half of the test silently set up the second:
`test_progress_is_monotonic_below_terminal_and_completed_forces_100` asserted a
lower clamp that does not exist — the `99.0` it checked was the *earlier* value
being kept, and a fresh job handed `-3.0` reports `0.0`. Nothing in the combined
test could have told you that.

Why: the failing test's name tells you which behaviour broke; each test is
independently skippable; setup is paid per fixture, not per test, so splitting
costs nothing. Applies equally to every tier and to Playwright specs — the
Playwright real suite's lifecycle specs are the one deliberate exception,
because each one *is* a single headline flow.

Common conflations: "captures X and Y" → one test per dimension; "returns 200
and writes to DB" → response shape vs. side effect; "handles success and
failure" → always split; "sets header and forwards body" → one per output
channel.

### Anti-patterns

- **Superficial contract test** — a `contract/` or `e2e/` test that boots
  the emulator, then patches the provider client. Either drive the real fake
  or move to an integration test.
- **Mocking the DB** — never. `db_session` is real and cheap. A `MagicMock`
  session tests nothing about `scopes`, cascades, or constraints.
- **Asserting on mock call arguments as the outcome** —
  `mock.assert_called_with(...)` tests wiring. Assert the row, the response,
  the storage object, the `Recorder` entry. (Asserting the *request the
  emulator received* is fine — the boundary is the observable.)
- **Mirroring the implementation** — tests derived from reading the source
  pass by construction. Derive from requirements.
- **Hand-written `deleted_at.is_(None)` in test queries** — use
  `scopes.live()` / `scopes.trashed()` in tests too; a hand-rolled predicate
  drifts from the production one.
- **Mocking static registries** — `PROVIDERS`, `Capability`, `FileType`,
  `queryKeys`. Mock the *service* that reads them, never the constants.
- **Order-dependent tests** — `pytest-randomly` shuffles and `xdist`
  parallelises. A test that passes only after another ran has shared mutable
  state: a module-level singleton not reset in `_patch_engine`, an
  `_overlay` key set without cleanup, a file written outside `tmp_path`. Fix
  the state, not the order.
- **A tier violation in disguise** — a file under `tests/unit/` that takes
  `db_session`, or a file under `unit/`/`integration/` that opens a socket
  (the socket guard fails it). Move it to the tier whose directory says what
  it does; don't widen the guard.
- **A topic-named test file** — `test_new_features.py`, `test_pure_helpers.py`,
  `test_api_hardening.py`. Tests live in the mirror of the unit they defend;
  a file that spans units gets split into their mirrors.
- **Cross-test collisions on the shared Playwright DB** — the real suite runs
  serially on one DB and only wipes it per *launch*. Any name a spec writes
  must be per-run unique (`` `e2e-model-${Date.now()}` ``) and the spec cleans up
  what it created.
- **Implicit "does not raise"** — when not-raising *is* the contract (best-
  effort cleanup, GC that must not propagate), assert it explicitly: the
  return value, the unchanged row, `assert caplog` empty of errors. A bare
  call that happens not to raise is an accidental assertion with a confusing
  failure message.
- **Real secrets or access codes in fixtures** — never. Use obviously fake
  values (`"12345678"`, `"key"`).
- **Skipping the contract fallout** — a response-shape change updates
  `tests/fixtures/openapi_contract.json` (`UPDATE_OPENAPI_CONTRACT=1`) and,
  for provider contracts, `frontend/src/generated/printer-contracts.ts` via
  the codegen `--check`. Regenerating without reading the diff hides an
  accidental API break.

## Test data

- **Round numbers** (100, 50, 25) for calculations; **absolute instants**
  (`"2026-01-01T00:00:00Z"`) instead of `now() ± offset`.
- **Build rows through the `make_*` fixtures**, never by constructing
  `Model(...)` / `Printer(...)` / `User(...)` inline and never through a
  module-local helper. `tests/factories/` is the one place that knows how library
  state is encoded, and its keywords (`trashed=`, `provider=`, `recommended=`,
  `scanning=`, `uploaded=`) exist because the raw columns silently mislead: a
  wrongly-encoded row inserts cleanly and is then invisible to the code under
  test. Builders create-or-fail and generate their own unique columns — never
  create-or-reuse, which hides collisions, and never a hard-coded slug or sha,
  which collides on the second row. Full rules and the maintenance contract:
  [references/fixtures.md](references/fixtures.md).
- **Real slicer files** live in `backend/tests/fixtures/` (Orca, Prusa, Cura,
  Bambu Studio, real MK4/Ender-3 outputs). Reach for one before hand-writing
  G-code; hand-write only when the header under test must be minimal.
- **Unique bytes per model** where content-hash dedupe applies — embed the
  model name in the G-code/STL so two uploads stay two models.

## What NOT to test

Simple getters, third-party behaviour, generated code (Alembic scaffolding,
`printer-contracts.ts`, `types/` from OpenAPI), and components that only
render data without logic.

## Validate before you report

Backend: `cd backend && ./scripts/test.sh fast -q` for the loop,
`./scripts/test.sh full -q` before claiming green, and `./scripts/test.sh
coverage` when the change touches `app/` — that lane is what CI gates on, and it
is the only one that runs the coverage floors. Then `uv run ruff check app/
tests/` and `uv run pyright`.

`printstash-core`: `cd backend/packages/printstash-core && ./scripts/test.sh
coverage`.

Frontend: `pnpm lint && pnpm format:check && pnpm typecheck && pnpm test`, plus
`pnpm coverage` when the change touches `src/` or either workspace package.

Report the exact result — never say tests passed without running them, and paste
failures verbatim. A coverage floor that had to be raised is part of the result;
say which one and to what.
