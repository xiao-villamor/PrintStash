# Backend tests (pytest)

Conventions for `backend/tests/**` and `backend/packages/printstash-core/tests/**`.
The policy (matrix, tiers, file anatomy, parametrization rules) is in
[SKILL.md](../SKILL.md); this file is the runtime-specific how.

## Layout

```
backend/tests/
  conftest.py            shared fixtures: engine, app, client, auth, isolation; marker-by-directory
  paths.py               FIXTURES_DIR / TESTDATA_DIR / ALEMBIC_INI / REPO_ROOT — never `parents[2]`
  _guards.py             the tier guards the unit/ and integration/ conftests install
  unit/                  pure logic · no db_session/client · mirrors app/
    conftest.py          guard: taking db_session/client or opening a socket fails
    core/test_<module>.py          ↔ app/core/<module>.py
    services/test_<module>.py      ↔ pure helpers of app/services/<module>.py
  integration/           real in-process app + DB + storage, egress stubbed · DEFAULT · mirrors app/
    conftest.py          guard: any real network connection fails
    api/v1/test_<router>.py        ↔ app/api/v1/<router>.py
    services/test_<service>.py     ↔ app/services/<service>.py
    db/test_<module>.py            ↔ app/db/<module>.py (incl. test_migrations.py)
    schemas/test_<module>.py       ↔ app/schemas/<module>.py
    postgres/test_<contract>.py    @pytest.mark.postgres — dialect + concurrency gate
  contract/              our clients vs contract-enforcing fakes over a real loopback socket · mirrors app/
    services/test_<provider>.py    emulator / MQTT-FTPS / OIDC fakes
    services/test_storage_backend.py   @pytest.mark.s3 — a real SeaweedFS container
  e2e/                   whole app via ASGITransport + fakes
    conftest.py
    test_<flow>.py                 one headline flow per file
  fakes/                 emulators + contract fakes, shared by contract/ and e2e/
  fixtures/              data files: real G-code, meshes, openapi_contract.json
  repo/                  repo-level invariants: OpenAPI snapshot, CI config, import boundaries
```

- **Directory = tier.** `conftest.py::pytest_collection_modifyitems` marks
  `e2e` and `contract` from the path; there is no `integration` marker.
  **Resource markers** gate subsets: `postgres`, `s3` (both resolved through
  `tests/containers.py` — see *Real services* below), `slow` (large real files,
  long simulations). All markers are registered in `pyproject.toml`;
  `--strict-markers` is on.
- **Lanes** (`./scripts/test.sh`): `fast` = `tests/unit tests/integration -m
  "not slow"` · `contract` · `e2e` · `full` = everything. CI runs `full` with the
  coverage gate.
- Every directory is a package (`__init__.py`) so same-basename files in
  different tiers coexist.
- **Never compute a path from `__file__`.** `Path(__file__).resolve().parents[2]`
  hard-codes how deep the file sits, so it breaks the day the file moves — for a
  reason unrelated to what it asserts. Import the anchor instead:
  `from tests.paths import FIXTURES_DIR, TESTDATA_DIR, ALEMBIC_INI, REPO_ROOT`
  (`printstash-core` has its own `tests/paths.py`; reach it relatively,
  `from ..paths import FIXTURES_DIR`).
- A production module with more behaviour than one ~600-line file can hold
  becomes a folder named after it (`integration/api/v1/printers/test_create.py`,
  `test_rbac.py`, `test_control.py`), split by endpoint/method group. The
  folder is still the mirror; the split is by unit inside the module.
- `printstash-core`: `tests/<subpackage>/test_<module>.py` ↔
  `src/printstash_core/<subpackage>/<module>.py`; testkit tests under
  `tests/testkit/`. May not import `app` (`tests/repo/test_forbidden_imports.py`).

## File anatomy (pytest specifics)

Follow the anatomy in SKILL.md. In pytest that means:

```python
"""``persist_artifact`` writes one artifact, or nothing at all.

It used to commit the File row before the thumbnail and Metadata; a failure in
between left a model that rendered but had no print time or cost.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from app.db.models import File, FileType, Metadata, Model
from app.services import ingestion

FROZEN_NOW = "2026-01-01T00:00:00Z"
BLOB_HASH = "b" * 64


@pytest.fixture
def model(db_session: Session) -> Model: ...        # shared by ≥2 tests


def _persist(db_session: Session, model: Model, **overrides): ...  # builder


class TestPersistArtifact:                          # ↔ ingestion.persist_artifact (integration/services/test_ingestion.py)
    def test_persists_a_file_row_with_its_metadata_in_one_commit(self, db_session, model, staged):
        file_row = ingestion.persist_artifact(db_session, model=model, staged_path=staged, ...)

        md = db_session.exec(select(Metadata).where(Metadata.file_id == file_row.id)).one()
        assert md.estimated_time_s == 120

    def test_rolls_back_the_file_row_when_metadata_fails(...): ...   # Error rows last


class TestCanonicalPath:                            # next unit in the module
    ...
```

- Groups are `class Test<Unit>` in the order the production module defines
  the units; flat functions are fine for a module with a single public
  function.
- Fixtures are typed and return concrete rows; `db_session` participates so
  the row is committed and refreshed.
- Waiting is never `time.sleep` in `unit/` or `integration/`; drive the entrypoint. In `e2e/`,
  a bounded poll with a hard failure (`_await_job`) is the pattern.

## Parametrize mechanics

```python
@pytest.mark.parametrize(
    ("name_len", "status"),
    [
        pytest.param(1, 201, id="min-length"),
        pytest.param(MAX_NAME, 201, id="max-length"),
        pytest.param(MAX_NAME + 1, 422, id="over-max"),
    ],
)
def test_validates_model_name_length(client, auth_headers, name_len, status): ...

@pytest.mark.parametrize("provider", list(PROVIDERS), ids=lambda p: p.value)  # from the registry
def test_every_provider_reports_capabilities(provider): ...

@pytest.fixture(params=["local", "s3"], ids=str)      # sweep every backend
def storage(request, tmp_path): ...
```

- `pytest.param(..., id=...)` or `ids=` on every parametrize; ids read as the
  variant, not as data.
- Stack two `parametrize` decorators only when every combination is a real
  case; otherwise list the meaningful pairs explicitly.
- Registry-derived lists over hand-copied ones; the test then guards the
  registry.
- `pytest.param(marks=pytest.mark.xfail)` is not a way to keep a red case
  quiet — the case is a `❌` row with an issue.

## Fixtures you get for free (`tests/conftest.py`)

| Fixture | Gives you | Use for |
| --- | --- | --- |
| `db_session` | a `Session` on the shared in-memory SQLite engine (production pragmas, `foreign_keys=ON`) | every in-process service/query test |
| `client` | `TestClient(app)` with hub, provider registry, `LocalTaskQueue` attached | every router test |
| `auth_headers` | bearer header for a fresh superuser with `admin` scope | admin happy paths only — it proves nothing about the 403 half of a contract |
| `user_headers` | headers for a fresh **non**-superuser, at the scope you name | every RBAC and scope row; two identities means two calls |
| `make_user` + `headers_for` | the user row *and* its headers | when the test also grants that user a role |
| `app`, `hub` | the FastAPI app / a `PrinterHub` on `InProcessBus` | direct hub/service driving |
| `threaded_hub_db` | swaps in the shared-cache engine so real `asyncio.to_thread` writes can race the test's reads | tests that run the *real* polling loop |
| `tmp_path` (autouse chdir) | cwd is a throwaway dir | anything writing relative paths |

Every test is isolated by the autouse `_patch_engine`: session factory
override, `_overlay.clear()`, storage-root wipe, truncate all tables, rebind
`LocalStorageBackend`, drop the cached `httpx.AsyncClient`, reset the login/
refresh rate limiters. **Adding a module-level singleton to `app/`? Add its
reset to `_patch_engine` in the same PR** — otherwise it leaks under
`pytest-randomly` and shows up as an order-dependent failure far from its cause.

## Building rows: the `make_*` fixtures

`tests/integration/conftest.py` exposes a session-bound builder for every table a
test needs — `make_model`, `make_file`, `make_printer`, `make_collection`,
`make_inbox_item`, `make_external_library`, and the rest — plus promoted
scenarios (`a_model_with_gcode`, `a_printer_with_a_queue`). They are the arrange
step: **never construct a row inline, and never add a module-local `_make_*`
helper.** `uv run pytest --fixtures -q tests/integration` lists them;
`tests/factories/__init__.py` is the inventory.

Their keywords name *state*, not columns — `trashed=`, `provider=`,
`recommended=`, `scanning=`, `uploaded=` — because each encoding is one a
hand-built row gets wrong in a way that **inserts cleanly and is then invisible
to the code under test**, so the test passes against a path it never reached.

Changing a production entity means changing its builder, protocol and fixture in
the same PR. Rules, the maintenance table and the PR checklist:
[fixtures.md](fixtures.md).

`tests/unit/conftest.py` and `tests/integration/conftest.py` install a
**socket guard**: any real network connection raises. A test that needs a
socket is a contract test — move it to `contract/`. `unit/conftest.py`
additionally fails a test that requests `db_session`/`client`: that's an
integration test in the wrong directory.

E2E fixtures (`tests/e2e/conftest.py`): `e2e_db` (on-disk SQLite so app
handlers and worker threads get their own connections), `api` (async
`httpx.AsyncClient` over `ASGITransport`), `fakes` (`Recorder` + `base_url`
for Discord/ntfy/webhook/Telegram targets, `flaky_webhook_url(key)`),
`superuser_headers`. Drive `notifications.dispatch_due()`, hub methods, and
scan functions directly — background loops are deliberately not started.

## Configuration in tests

`Settings` reads `VAULT_*` once at import. Per-test configuration goes through
the overlay, never `os.environ`:

```python
from app.core.config import _overlay

def test_purges_trash_after_retention(monkeypatch, db_session):
    monkeypatch.setitem(_overlay, "trash_retention_days", 0)
    ...
```

`monkeypatch.setitem` restores on teardown and `_patch_engine` clears the
overlay before the next test. Storage dirs: `_overlay["data_dir"] = tmp_path /
"files"` then `bind_backend(LocalStorageBackend())` / `get_backend()`.

## Standing in for egress

Only outbound boundaries are ever faked, and only under `unit/` and `integration/`:

- **Outbound HTTP** — patch `get_http_client` **where it is used**, not where
  it is defined: `patch("app.services.moonraker.get_http_client")`, then
  `.return_value.request = AsyncMock(return_value=resp)` /
  `side_effect=[...]`. Build responses as real `httpx.Response` (preferred) or
  `MagicMock(status_code=..., json=..., text=...)`. Assert the *reaction*
  (raised `ProviderError`, persisted state) — and the request URL/body only
  when the request *is* the contract under test.
- **Provider transports with constructor seams** — inject the fake:
  `BambuLanProvider(..., mqtt_client_factory=factory)` from
  `tests/fakes/mock_bambu.py`; `PrintSim(monotonic=lambda: now[0])` for a
  controllable clock.
- **Clock** — inject via the seam the code offers (`PrintSim.monotonic`,
  `app.core.time`), or `monkeypatch.setattr` at the using module. Pin
  absolute instants; never derive expectations from `datetime.now()`.

Never patch inside `contract/` or `e2e/`. Those tiers reach faults through
the fakes' own flags.

## Emulators and the testkit

`printstash_core_testkit` (in `backend/packages/printstash-core/src/`) is the
shared toolbox; `tests/fakes/*` re-exports it and adds the transports that
need app-side types. Contract tests build the fake, `start_server` it, point
the real client at `base_url`, and assert on the `Recorder`:

| Need | Reach for |
| --- | --- |
| Real loopback ASGI server for a fake | `start_server(app) -> RunningServer(base_url, stop)` |
| What the fake received, with call counts | `Recorder` / `Received` |
| A printer that progresses in wall-clock or injected time | `PrintSim(total_mm, total_seconds, print_seconds, monotonic=)`; states `STANDBY/PRINTING/PAUSED/COMPLETE/CANCELLED/ERROR` |
| Moonraker + Spoolman, PrusaLink, OctoPrint apps | `mock_printer.create_app`, `mock_prusalink.create_app`, `mock_octoprint.create_app` |
| Notification targets (Discord/ntfy/webhook/Telegram, flaky) | `build_provider_app(recorder)` |
| Bambu MQTT/FTPS, Centauri SDCP, OIDC provider | `tests/fakes/mock_bambu.py`, `mock_centauri.py`, `mock_oidc_provider.py` |

Faults are flags on the fake (`reject_commands=True`,
`expected_access_code=...`, `--auth-mode`, `/flaky/{key}`, a `PrintSim` driven
to `ERROR`). When the fault you need has no flag, add one to the fake — that's
a contract-fake improvement, not a test hack.

A new provider: add its credentials row to `FULL_CREDENTIALS` in the
conformance pack (`integration/services/printer_provider/test_conformance.py`;
it fails until you do), write `integration/services/test_<provider>.py` for
normalisation, `contract/services/test_<provider>.py` against its emulator,
and one `e2e/` flow. Update `docs/provider-support.md` for the
support level you actually proved.

## Async

`pytest-asyncio` is in strict mode: mark coroutine tests `@pytest.mark.asyncio`
(e2e style) or call `asyncio.run(coro)` inside a sync test (provider-client
style). The cached outbound client is dropped per test, so a fresh
`asyncio.run()` loop never inherits one bound to a dead loop.

## Concurrency

- Real cross-thread DB traffic (hub polling loop + test reads): `threaded_hub_db`.
- Simultaneous writes / unique-constraint races: `ThreadPoolExecutor` +
  `threading.Barrier` so both writers reach the statement together
  (`integration/postgres/`, `@pytest.mark.postgres`). Assert the row count and the `IntegrityError`,
  not timing.

## Real services (`postgres`, `s3`)

Two markers need a service the suite cannot fake, and `tests/containers.py` starts
it: a pinned `postgres:16-alpine` and a pinned SeaweedFS. **That is the only
path** — no environment variable, no `docker run`, no compose file. The image, the
command and the readiness check are written once, so a local run and a CI run are
the same run.

**No Docker is an error, not a skip.** If a selected test carries one of these
markers and no daemon is reachable, `pytest_collection_modifyitems` stops the
session with a message naming the prerequisite. This is the rule the whole
arrangement exists for: the previous setup passed locally with 21 tests quietly
absent, and those 21 were the dialect-sensitive SQL, the migration path
self-hosters upgrade through, and the S3 storage and backup destinations. A run
that verified none of that must not look like a run that did. Selecting a subset
that needs neither service (`./scripts/test.sh fast`, or any single unit file) is
unaffected — the check only fires for markers a *selected* test carries.

Containers start lazily, on the first selected test that needs one, and stop once
at session end. A run that touches neither starts nothing.

**Writing one of these tests.** Carry the marker (or live in
`integration/postgres/`, which gets it from the directory) and read the endpoint
through `postgres_url()` / `s3_endpoint()` **inside a fixture or test body** —
never as a module-level constant. A module-level read happens at import, before
the resolver has started anything.

**What is deliberately not converted.** `scripts/test_minio_migration.sh`
rehearses a compose-level migration an operator performs by hand, and its value is
that it runs the same commands they would; wrapping it in a Python container API
would make it a different procedure from the one it documents. The production and
manual-testing compose stacks are likewise not test infrastructure.

Add a `postgres` case when a change introduces a query, index, constraint, or
migration whose behaviour can differ from SQLite (JSON operators, `ON CONFLICT`,
boolean/enum handling, batch alters). Add an `s3` case when a change touches
storage keys, conditional writes, version ids, or ETag handling — those are
properties of the object store, and a stub that returns what we expect proves only
that we expect it.

## Contract snapshots (`tests/repo/`)

- `fixtures/openapi_contract.json` — any router/schema change fails the
  OpenAPI snapshot test. Regenerate with `UPDATE_OPENAPI_CONTRACT=1 uv run
  pytest tests/repo`, **read the diff**, and mention the API change in the PR
  Notes. Additive only within 0.x.
- `frontend/src/generated/printer-contracts.ts` — provider capability changes
  in `printstash_core` regenerate via `python -m
  printstash_core.printers.codegen --output ... --check` (CI enforces).

## Coverage

`./scripts/test.sh coverage` — `full` under branch coverage, then the gate. Two
pytest invocations, on purpose: the gate reads `coverage.json`, and that file is
only written once the run it measures has finished, so a gate collected into the
measured run could only ever judge the *previous* run's numbers. That is how a
coverage check passes on a report predating the change it was meant to judge.
The gate carries `pytest.mark.coverage_gate` and every other lane deselects it.

Three outputs, use all three:

- **term-missing** in the terminal — `1234, 1240-1243` are uncovered statements;
  `146->136` and `142->exit` are partial branches, the arrow naming the jump that
  never happened.
- **`.coverage-html/index.html`** — per line and per branch, clickable, and the
  only readable way into a module with many partials.
- **`coverage.json`** — what the gate reads. Also the fastest way to answer "which
  modules are worst?" without scrolling 130 rows of terminal output.

`branch = true` is set in `[tool.coverage.run]`, which is what makes the numbers
mean anything: under line coverage an `if x:` whose false path never runs counts
as covered. The same suite reads **95.07% by lines and 93.35% with branches** —
612 conditions that only ever went one way. `concurrency = ["thread",
"multiprocessing"]` and `parallel = true` are there because the suite runs under
xdist and the app uses worker threads; without them coverage loses the lines
executed off the main thread, which reads as a gap in exactly the code that is
hardest to test.

### The gate: `tests/repo/test_coverage_floors.py`

Not `--cov-fail-under`, which can only check the aggregate — and at 21,000
statements the aggregate is blunt enough that a 900-line service can fall from
95% to 70% and move the total by half a percent. Every module below 90% got
there that way. The gate is therefore:

- **`TOTAL_FLOOR`** — the aggregate, two-sided. Fall below it and the run fails;
  clear it by more than `TOTAL_SLACK` and the run also fails, telling you to
  raise it. A floor nobody is forced to move stops being a gate.
- **`MODULE_FLOOR = 90`** — every module on its own.
- **`PINNED_BELOW_FLOOR`** — the debt: modules that were already under the floor,
  each pinned at what it measures so it cannot slide further. `MAX_PINNED` caps
  the list, two-sided, so it may only shrink. A module that reaches
  `MODULE_FLOOR` fails the gate until its entry is deleted and the cap lowered.
- **`test_covers_the_whole_application_package`** — a module that no test imports
  appears in no report, and an absent row reads as 100% rather than the 0% it is.

If your change touches a pinned module, clearing its debt is in scope. Adding a
new pin is not: the answer to a module under the floor is the test.

`# pragma: no cover` is close to retired. It used to hide the `s3` paths, which
now run in every `full` because the container starts itself — removing seven of
them uncovered 413 statements that the gate had been counting as tested. Do not
add one without a sentence in the PR saying what makes the code unreachable from
a test, and never to skip a hard case.

### printstash-core

Its own lane, because the package installs and tests independently of the
backend and that independence is the point: `cd packages/printstash-core &&
./scripts/test.sh coverage`. Same two-pass shape, same gate file, floors set much
higher — 1,444 tests against 3,346 statements with no database, no sockets and
nothing to stand in for, so `MODULE_FLOOR` is 96 and there is no debt list. When
something lands under the floor here, there is no input it cannot be handed
directly; write the test.

## Lint and types

`uv run ruff check app/ tests/` and `uv run ruff format app/ tests/` apply to
tests too; `uv run pyright` covers `app/`. Type your helpers
(`-> dict[str, str]`), keep `from __future__ import annotations` at the top,
and import inside a test body only when the import has side effects the
docstring explains.

## One trap worth knowing: `SQLModel.metadata` is shared, mutable state

Not a rule, a scar. `app/db/models.py` has a foreign-key cycle (`files.model_id ->
models.id`, `models.thumbnail_file_id -> files.id`). SQLAlchemy breaks it per
dialect, and on one that supports `ALTER TABLE ... ADD CONSTRAINT` it lifts those
constraints out of `CREATE TABLE` and sets `ForeignKeyConstraint._create_rule` to
stop them rendering inline.

That attribute lives on the **metadata**, not the engine. So one `create_all`
against PostgreSQL changes what every later `create_all` in the process emits — and
SQLite, which cannot ALTER, just loses the constraints. `files` goes from three
inline foreign keys to none.

It surfaced as a flake with no plausible cause: `tests/e2e` green on its own,
`tests/e2e tests/integration/postgres` failing two runs in five, always the same
backup-restore test, always `OrphanSchemaError`. Nothing was wrong with either test,
and which tests shared an xdist worker decided whether it happened.

The general shape, which will happen again with something else: **a test that
configures a process-wide singleton has to put it back.** The engine, the session
factory, the storage backend and the rate limiters are all handled by autouse
fixtures in `tests/conftest.py` for exactly this reason; `SQLModel.metadata` was the
one nobody had noticed was in that category. When you add a test that touches a real
PostgreSQL, a real container, or anything else the rest of the suite shares, ask what
it leaves behind — and if the answer is "nothing", prove it with a `tests/repo/`
tripwire rather than a comment. `tests/repo/test_schema_ddl.py` is that tripwire for
this one.
