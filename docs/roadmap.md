# Roadmap

This is the working roadmap for PrintStash. It is intentionally practical: the
project should stay useful for home labs, single printers, and small farms before
it grows into anything heavier.

Roadmap feedback belongs in
[the public roadmap discussion](https://github.com/xiao-villamor/PrintStash/discussions/1).
Issues are better for confirmed bugs or scoped implementation work.

## Current App Stage: 0.1 Initial Self-Hosted Release

Stage 4 production hardening is implemented. The app is currently in the 0.1
initial self-hosted release stage: useful for local-first 3D print library
workflows, installable through Docker Compose, and ready for real homelab
feedback. SQLite plus local filesystem storage remain the default path.
Postgres, S3/R2-compatible storage, cloud backups, and provider adapters are
available as optional deployment paths.

Developed features in the current app:

- STL, 3MF, and G-code ingestion through the web UI, REST API, and OrcaSlicer post-processing hook
- G-code parser coverage for OrcaSlicer, PrusaSlicer, Bambu Studio, Cura, and Klipper/Orca samples
- Content-hash deduplication, logical model grouping, version history, thumbnails, and in-browser STL preview
- Categories, tags, search, model editing, printer-presence filters, and model-to-printer file badges
- G-code revision upload, labels, outcome status, notes, recommended marker, and metadata comparison
- First-run setup wizard, API-key script auth, JWT UI login, refresh/logout flow, role-aware admin access, and audit logs
- Alembic migrations, optional Postgres support, SQLite-to-Postgres migration script, and documented upgrade flow
- Local and optional S3/R2 storage, multipart S3 uploads, pre-signed downloads, lifecycle policy configuration, and backup/restore endpoints
- Operational health output for database, storage, backup, and printer provider readiness
- Moonraker/Klipper provider with live status, upload/send, optional start, pause/resume/cancel, printer file inventory sync, remote-file start, and job history
- Bambu LAN beta provider with local status plus pause/resume/cancel controls; upload/send, remote file inventory, and remote-file start remain unsupported
- Responsive Next.js UI for the library, model detail, upload, taxonomy management, settings, setup, printer list, and printer detail workflows

## Now: Release Validation and Feedback

Goal: keep the initial self-hosted release easy to install, easy to upgrade, and
safe enough for real home use.

- Publish and validate tagged release notes and Docker image guidance
- Collect real-world feedback from Docker/NAS/homelab installs
- Exercise backup/restore, upgrade, and provider diagnostics on fresh installs
- Add parser fixtures from more slicers and printer profiles as users share samples
- Improve first-run setup and error messages where new users get stuck
- Keep install/upgrade notes repeatable across tagged releases

## R1: Provider Maturity

Goal: make printer integrations predictable across normal home setups.

- Bambu LAN upload/send parity, with provider-safe guardrails
- Broader hardware validation for provider-level diagnostics
- More disconnect/reconnect coverage for mixed fleets
- Clear UI states for unsupported printer actions
- Hardware notes for tested Moonraker and Bambu setups

## R2: Operations Hardening

Goal: make backup, restore, upgrades, and monitoring less scary.

- Scheduled release backup/restore smoke checks
- Better health output for database, storage, S3, and printer providers as installs get more varied
- Structured metrics for request latency, ingestion, and printer status
- Background job cleanup that is easier to reason about across restarts
- Upgrade notes for SQLite and optional Postgres installs

## R3: Library Workflow Polish

Goal: make the vault better as a daily-use 3D print library.

- Better bulk editing for tags/categories and revision labels
- Saved filters or views for common searches
- Cleaner model/version comparison beyond the initial G-code metadata compare
- More useful model detail pages for repeated reprints
- Import/export paths for people migrating from folders or other tools

## R4: Fleet and Scheduling

Goal: help small printer farms without turning PrintStash into a full slicer or
queue manager.

- Optional routing strategies: manual, default printer, least busy
- Queue visibility with a provider-normalized job model
- Printer maintenance windows and soft-drain mode
- Alert hooks for offline/error/completed state transitions

## R5: Optional Cloud-Ready Adapters

Goal: support larger installs while keeping the default path self-hosted and
local-first.

- More complete Postgres deployment guidance
- S3/R2 lifecycle policy templates
- Multi-tenant/org routing only if there is real demand
- Cloud printer/provider adapters only when they do not compromise local-first use

## Not Planned Right Now

- A slicer
- A public cloud service
- Hard dependency on Postgres, Redis, S3, or external queues
- Replacing Moonraker/Bambu firmware as the source of truth for print execution
