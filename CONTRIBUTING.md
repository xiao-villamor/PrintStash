# Contributing to PrintStash

Thanks for taking a look. PrintStash is still early, so the most useful
contributions are clear bug reports, real deployment notes, printer/provider
testing, parser fixtures, and small focused PRs.

## Before You Open a PR

- Search existing issues and discussions first.
- Open an issue for behavior changes, larger features, or anything that touches
  the data model/API.
- Keep changes small enough to review in one sitting.
- Do not include secrets, printer access codes, private URLs, or real API keys in
  issues, logs, screenshots, fixtures, or tests.

## Development Setup

Backend:

```bash
cd backend
uv sync --extra dev

VAULT_DB_URL=sqlite:///./dev.sqlite \
VAULT_DATA_DIR=./_data/files \
VAULT_THUMB_DIR=./_data/thumbs \
uv run uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
pnpm install
pnpm dev
```

## Checks

Run the relevant checks before opening a PR:

```bash
cd backend
uv run pytest tests -v
uv run ruff check app/ tests/
uv run ruff format app/ tests/
uv run pyright
uv run ty check app/  # advisory while ty is pre-1.0
```

```bash
cd frontend
pnpm lint          # oxlint
pnpm format:check  # oxfmt
pnpm typecheck     # TypeScript 7 native compiler
```

## Performance experiments

The default development, build, and test commands remain the authoritative
paths. These opt-in lanes make timing experiments reproducible without making
experimental tools release requirements:

```bash
cd frontend
pnpm dev:bundle                 # Vite's experimental bundled development server
pnpm build:react-compiler       # native Oxc React Compiler production build
pnpm test:fast                  # audited pure tests: threads + shared module graph
pnpm test:changed               # root tests related to the current Git diff
pnpm test:happy-dom             # compatibility/timing trial; jsdom stays authoritative
pnpm test:jsdom                 # explicit authoritative root-suite comparison
pnpm test:e2e:bundle            # full mock-API E2E against bundled development
pnpm test:perf                  # 3 baseline production-build browser samples
pnpm test:perf:react-compiler   # same browser samples with native React Compiler
```

Compare the `PRINTSTASH_PERF` JSON emitted by the two browser timing commands.
Do not enable React Compiler by default based on build time alone: first triage
its unsupported diagnostics and require a repeatable interaction-time win.

The backend suite is split into lanes, and a lane is a directory: the tier a test
lives in *is* its tier. All parallel lanes use isolated worker databases/storage
and xdist work stealing:

```bash
cd backend
./scripts/test.sh --help          # the lane table, with what each one covers
./scripts/test.sh fast -q         # usual loop: tests/unit + tests/integration, minus `slow`
./scripts/test.sh affected -q     # dependency-based selection; first run seeds its cache
./scripts/test.sh contract -q     # our clients against fakes over a real loopback socket
./scripts/test.sh e2e -q          # the whole app over ASGITransport against the fakes
./scripts/test.sh full -q         # complete pre-merge gate
./scripts/test.sh serial -q       # diagnostic reference only
```

The `postgres` and `s3` subsets run against a real PostgreSQL and a real
SeaweedFS, started as containers for the run — so `full` needs Docker running,
and stops with a message naming the prerequisite if it is not. There is nothing
to configure. It is an error rather than a skip because a green run with those
tests absent verified neither the dialect-sensitive SQL nor the upgrade path.

`affected` stores only local dependency metadata in the ignored `.testmondata`
file. Treat it as a tight edit/test loop, not a substitute for `full`. Generic
S3 tests use SeaweedFS; MinIO is intentionally limited to the legacy
MinIO-to-SeaweedFS migration check, which pull requests run only when that
migration surface changes.

The manual **Tooling experiments** GitHub workflow runs the same lanes on a
hosted runner without adding experimental work to normal pull-request CI.

## Project Boundaries

- Self-hosted first. Cloud-style features should stay optional.
- API-first. The web UI should use the same API available to scripts.
- SQLite/local disk should remain the easiest path.
- Heavy mesh dependencies must stay lazy-loaded.
- The OrcaSlicer hook must never block an export because the server is down.

See [README.md](./README.md) for the deeper architecture notes.
