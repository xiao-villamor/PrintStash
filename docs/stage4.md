# Stage 4 — Production Hardening

**Codename:** Production Hardening
**Status:** implemented

## Goal

Ship the first tagged self-hosted release. Stage 4 prioritised safe upgrades,
predictable recovery, stronger authentication, deletion lifecycle controls,
and clearer deployment choices. SQLite and local filesystem storage remain the
default path; Postgres and S3 stay optional adapters for larger installs.

## Current Result

Stage 4 is implemented and the app is in the 0.1 initial self-hosted release
stage. The remaining work is release validation, real-world install feedback,
and provider maturity rather than core Stage 4 feature development.

Developed Stage 4 capabilities:

- Alembic-backed schema upgrades, optional Postgres support, and SQLite-to-Postgres migration tooling
- JWT refresh/logout, API-key script auth, role-aware admin access, and audit logs
- Soft-delete, restore, hard-delete, scheduled garbage collection, and orphan blob cleanup
- Local backup/restore, optional cloud backup, disaster-recovery docs, and operational health probes
- Optional S3/R2 storage with multipart uploads, pre-signed downloads, MinIO dev support, and lifecycle policy configuration
- First-run setup for admin user, storage choice, and backup settings
- Provider abstraction for Moonraker/Klipper and Bambu LAN with per-printer capabilities and diagnostics
- Moonraker file inventory sync, model printer-presence badges, remote-file start, and improved printer detail workflows
- API hardening for validation errors, unhandled errors, CORS defaults, upload/download blocking work, and printer send payload validation

---

## Execution Log

### Open-source launch prep

- [x] Refresh public-facing docs for a self-hosted release: README, roadmap, contribution guidance, issue templates, and GitHub discussion surface.
- [x] Add practical G-code revisions for 0.1: outcome labels, notes, recommended version, and metadata compare.
- [x] Add 0.1 release notes, upgrade guide, disaster recovery runbook, provider support notes, and release validation checklist.
- [x] Mark Bambu LAN as beta/status-control-only across capabilities, diagnostics, docs, and UI.

### Phase 4a — Schema and upgrade safety

- [x] Reframe Stage 4 around self-hosted production hardening
- [x] Add Alembic to the backend and create a baseline migration
- [x] Remove ad hoc SQLite column patching as the schema upgrade mechanism
- [x] Document an explicit upgrade flow for Docker and local installs
- [x] Add migration smoke tests against SQLite
- [x] Add Postgres drivers (`asyncpg`, `psycopg2`) to `pyproject.toml`
- [x] Implement `async_session()` on `SessionFactory` Protocol
- [x] Create `create_async_engine()` in `db/session.py`
- [x] Add Postgres service to `docker-compose.yml` as an optional profile
- [x] Write a SQLite→Postgres migration guide/script

### Phase 4b — Auth and admin hardening

- [x] Add `RefreshToken` model (token hash, user_id, expires_at, revoked)
- [x] Implement `POST /auth/refresh` and `POST /auth/logout` endpoints
- [x] Replace raw `Header` auth with FastAPI's `OAuth2PasswordBearer`
- [x] Add role-based access: enforce `is_superuser` on admin endpoints
- [x] Add `scope` to JWT payload (read/write/admin)
- [x] Implement in-memory token blocklist (invalidated on logout)

### Phase 4c — Data lifecycle and recovery

- [x] Add `deleted_at` column to File, Printer, PrintJob, User, Tag, Category
- [x] Add `deleted_by` FK to User on all soft-deletable tables
- [x] Implement `DELETE` endpoints for printers, categories, tags, users (soft)
- [x] Implement `POST /{resource}/{id}/restore` endpoints
- [x] Implement hard-delete endpoint: `DELETE /admin/{resource}/{id}?hard=true`
- [x] Implement scheduled GC background task (purge rows with `deleted_at < retention`)
- [x] Implement orphan file cleanup (delete blobs when DB records are purged)
- [x] Harden local backup + restore workflows and document recovery steps

### Phase 4d — Audit and observability

- [x] Add `AuditLog` model (actor_id, action, resource_type, resource_id, diff JSON, ip, timestamp)
- [x] Add `created_by` / `updated_by` columns to Model, Printer, PrintJob, Category, Tag
- [x] Implement SQLAlchemy event listener for auto-audit on writes
- [x] Implement `GET /api/v1/admin/audit` endpoint (admin-only, filterable)
- [x] Add audit log pagination and filtering by resource/resource_id

### Phase 4e — Optional deployment adapters

- [x] Add Postgres deployment docs with clear “when to choose it” guidance
- [x] Add S3/S3-compatible deployment docs with clear “optional feature” guidance
- [x] Add S3 health check probe to `/api/v1/health`
- [x] Implement multipart upload for files > 50MB in `S3StorageBackend`
- [x] Implement pre-signed URL generation for direct downloads
- [x] Add MinIO service to `docker-compose.yml` for local dev/test
- [x] Implement bucket lifecycle policy configuration (expiration, tiering)
- [x] Expand first-run setup to choose local/S3 storage and backup settings
- [x] Lazy-create external print sentinel rows only when an external job is captured
- [x] Fix authenticated frontend GET requests so admin pages do not falsely report expired tokens
- [x] Fix local thumbnail serving after upload by using the storage backend existence check and add STL/G-code ingest regression coverage

### Phase 4f — Provider abstraction (printer backends)

- [x] Add provider architecture (`moonraker`, `bambu_lan`) with shared interface
- [x] Keep existing `/api/v1/printers/*` API paths stable while dispatching by provider
- [x] Add per-printer capability exposure in `PrinterRead`
- [x] Add Bambu LAN local credentials fields + additive DB migration
- [x] Add Bambu LAN status + pause/resume/cancel support (send/upload deferred)
- [x] Add provider-focused unit/API coverage and preserve Moonraker regressions green
- [x] Fix Moonraker idle-state mapping to use `webhooks.state` fallback so configured printers no longer stick on `unknown` when `print_stats.state` is absent
- [x] Add Moonraker printer file inventory tracking with persisted sync, model badges, and printer file listings

### Phase 4g — API hardening audit

- [x] Add stable JSON handlers for request validation and unhandled errors, tighten CORS defaults, move upload staging/storage downloads off the FastAPI event loop, and harden printer send payload validation without changing successful response contracts.
- [x] Improve the Moonraker/Klipper printer status page with focused tabs, remote-file start actions, Vault printer filters, and a cleaner G-code revision upload/label flow.

---

## Deferred Beyond First Stable Release

- Multi-tenant organizations and workspace routing
- Automatic tenant scoping on every query
- Cloud-first storage namespacing and org-aware object layout

These remain valid future directions, but they are not part of the first
initial self-hosted release.

---

## Pre-existing foundation carried from Stages 1–3

| Component | Status |
|---|---|
| `S3StorageBackend` (all 16 methods) | 85% complete |
| `SessionFactory` Protocol with `async_session()` stub | Ready |
| SQLModel on all tables (Postgres-compatible) | Done |
| Basic JWT login (`/login`, `/me`, bcrypt) | Done |
| `system_config` runtime overlay (DB-backed config) | Done |
| Soft-delete on Model (`deleted_at` column) | Done |
| Backup S3 (separate destination bucket) | Done |
| Frontend domain-split types | Done |
