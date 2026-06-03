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

VAULT_API_KEY=devkey \
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
```

```bash
cd frontend
pnpm lint
```

## Project Boundaries

- Self-hosted first. Cloud-style features should stay optional.
- API-first. The web UI should use the same API available to scripts.
- SQLite/local disk should remain the easiest path.
- Heavy mesh dependencies must stay lazy-loaded.
- The OrcaSlicer hook must never block an export because the server is down.

See [README.md](./README.md) and [docs/adr](./docs/adr) for the deeper
architecture notes.
