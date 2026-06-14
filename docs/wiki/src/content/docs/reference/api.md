---
title: API
description: Authentication, the endpoints you'll actually use, and an upload example.
---

The same REST API under `/api/v1` powers the web UI, the OrcaSlicer hook, and any
script you write — there's no separate "internal" surface. Interactive OpenAPI
docs are served live by the backend, and they're the authoritative reference for
request and response shapes:

```
http://localhost:8000/docs     # Swagger UI
http://localhost:8000/redoc    # ReDoc
```

The tables below are a map to help you find your way around; `/docs` is the
contract.

## Authentication

Almost everything requires auth. However you authenticate, you end up holding a
JWT **Bearer token** that you send on every request:

```
Authorization: Bearer <token>
```

There are two ways to get one:

**1. Username + password** — what the UI does. Short-lived access tokens with
refresh-token rotation behind the scenes.

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<your-password>"}'
```

**2. Username + API key** — for scripts and automation, so you never bake an
account password into a long-lived hook. Create a named key under **Settings →
Access** (it's shown once — copy it then), then exchange it at the *same* login
endpoint for a Bearer token:

```bash
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<api-key>"}'
```

API keys can be revoked from the same Settings page without touching your
password, which is exactly why the Orca hook uses one.

## Endpoints you'll actually use

| Method | Path                                | Purpose                                 |
| ------ | ----------------------------------- | --------------------------------------- |
| `GET`  | `/api/v1/health`                    | Liveness + component readiness (no auth).|
| `POST` | `/api/v1/auth/login`                | Get an access token.                     |
| `GET`  | `/api/v1/models`                    | List models — filters, search, paging.   |
| `GET`  | `/api/v1/models/{id}`               | Model detail with files and metadata.    |
| `GET`  | `/api/v1/models/stats`              | Vault totals and breakdowns.             |
| `GET`  | `/api/v1/models/export`             | Metadata export (`?format=json` or `csv`).|
| `POST` | `/api/v1/ingest`                    | Upload files into the vault.             |
| `GET`  | `/api/v1/collections`               | List collections (with model counts).    |
| `GET`  | `/api/v1/tags`                      | List tags.                               |
| `GET`  | `/api/v1/printers`                  | List printers with live status.          |
| `GET`  | `/api/v1/printers/{id}/diagnostics` | Provider capabilities & connectivity.    |
| `POST` | `/api/v1/printers/{id}/send`        | Send a vault G-code file to a printer.    |
| `GET`  | `/api/v1/filament-profiles`         | List filament presets.                   |
| `GET`  | `/api/v1/printer-profiles`          | List printer presets.                    |
| `POST` | `/api/v1/backups`                   | Create a full backup (admin).            |
| `GET`  | `/api/v1/admin/users`               | User administration (superuser).         |

## Health check

The one endpoint with no auth, and the first thing to curl when something's off.
It breaks readiness out per component, so the response tells you *which* part is
unhappy:

```bash
curl http://localhost:8000/api/v1/health
```

It reports service identity and the readiness of the database, storage, backup,
and printer-provider subsystems.

## Upload example

Upload a file with a Bearer token. The ingest pipeline hashes the content,
deduplicates against what's already stored, and attaches the file to a model —
creating one if this mesh is new:

```bash
curl -X POST http://localhost:8000/api/v1/ingest \
  -H "Authorization: Bearer <token>" \
  -F "file=@./Benchy.gcode"
```

Because dedup is by content hash, re-uploading the identical file is a no-op
rather than a duplicate — handy when a hook fires twice.

For automated pushes after every slice, use the OrcaSlicer hook described in the
[user guide](/PrintStash/guides/user-guide/#skip-the-manual-upload-the-orcaslicer-hook).
