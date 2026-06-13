# Release Validation

Run these checks before tagging a release.

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
docker compose down
docker compose run --rm api uv run alembic upgrade head
docker compose up -d
```

Expected:

- existing models/files are still visible
- thumbnails still load
- 3MF/OBJ files can open through the cached STL preview endpoint
- a new G-code upload creates or updates the expected model
- Settings shows vault stats and the trash page can load

## Backend

```bash
cd backend
uv run ruff check app/ tests/
uv run pytest tests -v
```

## Frontend

```bash
cd frontend
pnpm lint
pnpm exec tsc --noEmit
pnpm build
```

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
- Read `docs/demo-walkthrough.md` and confirm current screenshots match the
  story being shown.
- Read `docs/community-starter-issues.md` and choose 3-5 issues to publish after
  the first tag.
