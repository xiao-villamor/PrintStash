# 0.1 Release Validation

Run these checks before tagging a 0.1.x release.

## Clean Install

```bash
cp .env.example .env
docker compose up -d --build
curl -fsS http://localhost:8000/api/v1/health
```

Expected:

- the setup page is reachable at `http://localhost:3000/setup`
- `/api/v1/health` returns version `0.1.0`
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
- a new G-code upload creates or updates the expected model

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
  URLs are not yet routed through Next image optimization.
- `stl-viewer.tsx` keeps the viewer controls effect scoped to the loaded model
  URL.

## Release Content

- Read `docs/known-limitations.md` and confirm the README links to it.
- Read `docs/demo-walkthrough.md` and confirm current screenshots match the
  story being shown.
- Read `docs/community-starter-issues.md` and choose 3-5 issues to publish after
  the first tag.
