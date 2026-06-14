---
title: API
description: Common REST endpoints, authentication, and an upload example.
---

PrintStash exposes a REST API under `/api/v1`. Interactive OpenAPI docs are
served at `/docs` (Swagger UI) and `/redoc` on the backend.

```
http://localhost:8000/docs
```

## Authentication

Most endpoints require authentication. Two schemes are supported:

- **Bearer token** — obtained by logging in; sent as `Authorization: Bearer <token>`.
- **API key** — created under **Settings**; suited to automation such as the
  OrcaSlicer upload hook.

```bash
# Log in and capture a token
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<your-password>"}'
```

## Common endpoints

| Method   | Path                                      | Purpose                               |
| -------- | ----------------------------------------- | ------------------------------------- |
| `GET`    | `/api/v1/health`                          | Liveness / version check (no auth).   |
| `POST`   | `/api/v1/auth/login`                      | Obtain an access token.               |
| `GET`    | `/api/v1/models`                          | List models (filters, search, paging).|
| `GET`    | `/api/v1/models/{id}`                     | Model detail with files and metadata. |
| `GET`    | `/api/v1/models/stats`                    | Vault totals and breakdowns.          |
| `POST`   | `/api/v1/ingest`                          | Upload files into the vault.          |
| `GET`    | `/api/v1/collections`                     | List collections.                     |
| `GET`    | `/api/v1/tags`                            | List tags.                            |
| `GET`    | `/api/v1/printers`                        | List printers with live status.       |
| `GET`    | `/api/v1/printers/{id}/diagnostics`       | Provider capabilities & connectivity. |
| `POST`   | `/api/v1/printers/{id}/send`              | Send a vault G-code file to a printer.|
| `GET`    | `/api/v1/filament-profiles`               | List filament presets.                |
| `GET`    | `/api/v1/printer-profiles`                | List printer presets.                 |
| `GET`    | `/api/v1/backup`                          | Backup/restore operations.            |
| `GET`    | `/api/v1/admin/users`                     | User administration (superuser).      |

Endpoint shapes and request/response models are authoritative in `/docs`; the
table above is a map, not a contract.

## Upload example

Upload a file with an API key. The ingest pipeline deduplicates by content hash
and attaches the file to a model (creating one if needed).

```bash
curl -X POST http://localhost:8000/api/v1/ingest \
  -H "Authorization: Bearer <api-key>" \
  -F "file=@./Benchy.gcode"
```

For automated G-code pushes after slicing, see the OrcaSlicer hook described in
the [user guide](/PrintStash/guides/user-guide/#pushing-g-code-from-orcaslicer).
