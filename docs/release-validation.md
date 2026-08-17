# Release Validation

Run these checks before tagging a release. For the full hands-on browser sweep
of every UI workflow, see [`manual-testing.md`](./manual-testing.md).

## Clean Install

```bash
cp .env.example .env
docker compose up -d --build
curl -fsS http://localhost:8000/api/v1/health
```

Expected:

- the setup page is reachable at `http://localhost:3000/setup`
- `/api/v1/health` returns the current app version
- health components include database, storage, backup, and printer providers
- Docker containers, networks, volumes, and default SQLite path use PrintStash
  naming for new installs

## Upgrade From Existing SQLite Volume

```bash
docker compose pull
docker compose up -d --wait
```

The API entrypoint applies migrations before serving traffic. Do not override
it with a separate `uv run alembic` command.

Expected:

- existing models/files are still visible
- thumbnails still load
- 3MF/OBJ files can open through the cached STL preview endpoint
- a new G-code upload creates or updates the expected model
- Settings shows vault stats and the trash page can load

## Backend

```bash
cd backend
uv sync --extra dev --extra full --frozen
uv run ruff format --check app/ tests/
uv run ruff check app/ tests/
uv run pyright
uv run deptry app
uv run pytest tests --cov=app --cov-report=term-missing --cov-fail-under=95
uv run bandit -r app -q
uv run pip-audit
```

## Frontend

```bash
cd frontend
pnpm lint
pnpm typecheck
pnpm test
pnpm build
pnpm test:e2e
pnpm test:e2e:real
```

## Optional database and storage contracts

- Run `tests/postgres` with `PRINTSTASH_TEST_POSTGRES_URL` against PostgreSQL
  16 through Psycopg 3.
- Run the async database contract once without extras (explicit capability
  error) and once with `--extra async-db` for SQLite async.
- Run `tests/test_storage_s3.py` against the pinned SeaweedFS service.
- Run `./scripts/test_minio_migration.sh`; it verifies normal, Unicode, and
  multipart objects twice with downloaded-content comparison.

## Image variants

Build both API variants, then enforce optional-package, size, and startup gates:

```bash
docker build --build-arg PRINTSTASH_VARIANT=full -t printstash-api-full backend
docker build --build-arg PRINTSTASH_VARIANT=lite -t printstash-api-lite backend
./scripts/check_api_image_variants.sh printstash-api-full printstash-api-lite
```

The publish workflows build both images for `linux/amd64` and `linux/arm64`.
The lite image must be at least 700 MiB smaller than full and may not start more
than 10% slower at the median.

CI also builds a loadable full ARM64 API image and tessellates the checked-in
valid STEP fixture under QEMU. Record native Raspberry Pi/ARM hardware and 1 GB
measurements separately; the emulated smoke proves wheel/import/runtime wiring,
not real-device performance.

Current intentional lint warnings:

- model thumbnails use plain `<img>` because authenticated/local API thumbnail
  URLs are served directly by the API.
- `stl-viewer.tsx` keeps the viewer controls effect scoped to the loaded model
  URL.

## Feature Smoke Checks

- Open Settings, confirm vault stats load, create a backup, and export JSON/CSV.
- Create and revoke an API key, then verify username plus API key can log in.
- Upload a mesh and G-code pair, open model detail, toggle mesh/G-code viewer,
  edit revision fields, and mark a recommended G-code.
- Soft-delete a model, restore it from Settings Trash, then soft-delete and
  purge it only on disposable data.
- Register or mock a Moonraker printer, sync files, and import matching print
  history into one model.
- Queue `POST /api/v1/files/thumbnails/rebuild` on a small library and poll the
  returned ingest job until completion.

## Release Content

- Read `docs/known-limitations.md` and confirm the README links to it.
- Read `docs/community-starter-issues.md` and choose 3-5 issues to publish after
  the first tag.
