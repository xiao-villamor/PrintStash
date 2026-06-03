# Roadmap

This is the working roadmap for PrintStash. It is intentionally practical: the
project should stay useful for home labs, single printers, and small farms before
it grows into anything heavier.

Roadmap feedback belongs in
[the public roadmap discussion](https://github.com/xiao-villamor/PrintStash/discussions/1).
Issues are better for confirmed bugs or scoped implementation work.

## Now: 1.0 Stable Self-Hosted Release

Goal: keep the stable self-hosted release easy to install, easy to upgrade, and
safe enough for real home use.

- Publish tagged release notes and Docker image guidance
- Collect real-world feedback from Docker/NAS/homelab installs
- Ship practical G-code revisions: outcome labels, notes, recommended version, and metadata compare
- Add parser fixtures from more slicers and printer profiles
- Improve first-run setup and error messages where new users get stuck
- Keep install/upgrade notes repeatable across tagged releases

## R1: Provider Maturity

Goal: make printer integrations predictable across normal home setups.

- Bambu LAN upload/send parity, with provider-safe guardrails
- Provider-level health diagnostics endpoint
- More disconnect/reconnect coverage for mixed fleets
- Clear UI states for unsupported printer actions
- Hardware notes for tested Moonraker and Bambu setups

## R2: Operations Hardening

Goal: make backup, restore, upgrades, and monitoring less scary.

- Backup/restore smoke checks and a disaster-recovery runbook
- Better health output for database, storage, S3, and printer providers
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
