# Roadmap

This is the working roadmap for PrintStash. It is intentionally practical: the
project should stay useful for home labs, single printers, and small farms before
it grows into anything heavier.

Roadmap feedback belongs in
[the public roadmap discussion](https://github.com/xiao-villamor/PrintStash/discussions/1).
Issues are better for confirmed bugs or scoped implementation work.

## Current Release: 0.9.0 — Provider Maturity

Production hardening is in place. The app is useful for local-first 3D print
library workflows, installable through Docker Compose (the default compose pulls
prebuilt GHCR images; a build overlay is available for contributors), and ready
for real homelab feedback. SQLite plus local filesystem storage remain the
default path. Postgres, S3/R2-compatible storage, cloud backups, and provider
adapters are available as optional deployment paths.

Developed features in the current app:

- STL, 3MF, OBJ, STEP/STP, and G-code ingestion through the web UI, REST API, and OrcaSlicer post-processing hook
- Import from URL or `.zip` (including Printables/MakerWorld/Thingiverse model pages resolved to their downloadable asset), SSRF-guarded and zip-slip/zip-bomb protected
- Shared volumes: mirror a server folder or NAS in place with two-way write-back, per-volume cron scheduling, and optional real-time watching with network-aware fallback
- Public, expiring, read-only share links to a single model (view-only by default; opt-in original-file download)
- Print statistics dashboard (cost, filament, prints, print time; time series + top collections/filaments) with a configurable display currency
- Measured prints: real filament + duration and per-print cost captured from Moonraker, with auto known-good revision promotion
- G-code parser coverage for OrcaSlicer, PrusaSlicer, Bambu Studio, Cura, and Klipper/Orca samples, including PrusaSlicer binary G-code (`.bgcode`) metadata and thumbnails
- Content-hash deduplication, logical model grouping, version history, thumbnails, cached STL conversion for 3MF/OBJ preview, and in-browser mesh/G-code previews
- Categories, tags, search, model editing, printer-presence filters, model-to-printer file badges, collection counts, collection moves, and drag-and-drop library organization
- G-code revision upload, per-revision labels, outcome status, notes, recommended marker, and side-by-side metadata comparison
- Filament and printer profiles (presets) for cost tracking and slicer defaults, managed from a dedicated Profiles page
- "Open in slicer" deep-links for OrcaSlicer, Bambu Studio, and PrusaSlicer straight from a model file
- Model print history with automatic Moonraker import for matching filenames and manual print-job logging
- First-run setup wizard, JWT auth for UI/scripts, refresh/logout flow, per-user API keys, role-aware admin access, and audit logs
- Alembic migrations, optional Postgres support, SQLite-to-Postgres migration script, and documented upgrade flow
- Local and optional S3/R2 storage, multipart S3 uploads, pre-signed downloads, cached mesh conversion, lifecycle policy configuration, and backup/restore endpoints
- Vault stats, storage usage reporting, configurable card metrics, trash retention controls, restore/purge actions, and thumbnail rebuild jobs
- Prometheus `/metrics` endpoint (request latency, ingestion counts, live printer status) and operational health output for database, storage, backup, background jobs, external-library scans, and printer provider readiness
- Moonraker/Klipper provider with live status, upload/send, optional start, pause/resume/cancel, printer file inventory sync, remote-file start, and job history
- Optional Spoolman integration (OFF by default): spool inventory display, per-print spool selection, consumption write-back on measured-print completion, and Moonraker-native-hook double-count detection
- Bambu LAN beta provider with local status, upload/send with explicit opt-in start, and pause/resume/cancel controls; remote file inventory remains unsupported
- PrusaLink and OctoPrint beta providers (status, upload/send, file inventory, pause/resume/cancel), plus Elegoo Neptune 4-family and Centauri Carbon/Carbon 2 support; every provider declares its capabilities, and the UI disables what a printer cannot do
- Responsive Vite/React UI with a light/dark theme toggle (system-preference aware) and a refreshed mobile layout: a five-slot bottom navigation with an overflow "More" sheet, on-canvas mobile search, slide-out filter drawers, and a floating action button across the library, model detail, upload, taxonomy, profiles, settings, setup, and printer workflows

The releases below are intentionally small: each is meant to be a single,
shippable step rather than a multi-month epic. Versions are indicative, not
promises, and the order can shift with real-world feedback.

## 0.7 — Notifications, Event Hooks, and Parser Robustness (delivered in 0.7.0)

Goal: tell people when something happens without making them watch a dashboard.

Shipped together in 0.7.0 rather than across several patches:

- Generic outbound webhooks for print-completed, print-failed, print-cancelled, and printer-offline events (cancellations are a distinct event so they can be muted separately)
- First-party targets: Discord, Telegram, and ntfy
- Per-event and per-printer notification toggles
- Delivery retry with backoff and a visible "last notification" status, off by default behind a master switch
- G-code parser robustness from real-slicer fixtures: fixes for OrcaSlicer filament-length corruption, Orca/Bambu bed-temperature detection, and PrusaSlicer infill detection
- Library-scan reliability hardening ([#24]): dense meshes are sized before loading and skipped above `VAULT_MESH_MAX_RENDER_TRIANGLES` (2,000,000 default) so a single high-poly model can no longer OOM-kill a scan; STL/3MF downloads stream off disk, and interrupted scans land in a terminal state instead of crash-looping
- Mesh thumbnails reworked: crease-aware smooth (Gouraud) shading and a Z-up 3/4 hero framing, matching the interactive viewer — which itself was fixed to stand models upright on the grid
- "Open in slicer" works on self-hosted instances ([#27]), and zip imports preserve the archive's folder structure as nested collections

[#24]: https://github.com/xiao-villamor/PrintStash/issues/24
[#27]: https://github.com/xiao-villamor/PrintStash/issues/27

Closed in later 0.7.x patches: the 0.7.0 binary-`.bgcode` follow-up — PrusaSlicer
binary G-code is now a supported file type, parsed for metadata and its embedded
thumbnail (preview/send stay disabled for it, as the toolpath is heatshrink-
compressed and printers want plain-text G-code) — delivered in 0.7.3.

## 0.8 — Spoolman Integration (delivered in 0.8.0)

Goal: track filament inventory and per-print consumption by integrating with
[Spoolman](https://github.com/Donkie/Spoolman) rather than reimplementing it.

Spoolman is mature, well-documented, self-hosted filament-inventory software with
a clean REST API, and our homelab audience already runs it. PrintStash already
captures real consumption per print (`PrintJob.filament_used_g`, derived from
Moonraker via `services/filament.mm_to_grams`), so the work is to *feed* that
inventory, not duplicate it. Spoolman becomes the source of truth for spools,
vendors, and remaining weight; PrintStash reads it for display and writes
consumption back.

This supersedes the earlier "build our own spool inventory" plan: a parallel
inventory would split the source of truth and ship a weaker copy of what
Spoolman already does well. Integration stays optional and OFF by default,
behind a master switch, with no hard dependency — consistent with the
local-first principles below.

Shipped in 0.8.0:

- Spoolman client + connection config (base URL, optional API key), behind a
  master switch and OFF by default; reachability surfaced in `/health` with
  graceful degradation that never blocks a print
- Read side: pull spools and show inventory + remaining weight in the Settings →
  Spoolman card
- Filament preset sync (one-way Spoolman → PrintStash): import Spoolman filaments
  as read-only `FilamentProfile` presets (cost/material/density/diameter), keyed
  by `spoolman_filament_id`, so filament data lives in one source of truth
- Spool selection when starting a vault job (send-to-printer) or logging a print
  manually, persisted on the print record and shown in print history; a synced
  spool drives exact per-print cost and density-accurate grams
- Write side: on measured-print completion, decrement the selected spool by
  `filament_used_g` (reuses the existing `print_results` + `mm_to_grams`
  pipeline). Moonraker-measured prints only — Bambu does not report live
  consumption
- Double-count safety: at write time, skip our own decrement when Spoolman
  reports an active spool that Moonraker's native hook is already counting (with
  a manual "write back anyway" override), plus a UI warning — so a print is
  never counted twice

## 0.9 — Provider Maturity (delivered in 0.9.0)

Goal: make printer integrations predictable across normal home setups, and
cover the other common homelab print stacks. This absorbs what was previously
listed as a separate "0.10 — More Providers" release: the provider work landed
together, so it ships as one release.

- Bambu LAN upload/send parity for plain-text Vault G-code, with idle-state
  guardrails and explicit opt-in start
- PrusaLink local FDM beta with Digest/API-key authentication, status, G-code
  upload/start, file inventory/deletion, and pause/resume/cancel
- OctoPrint beta provider: status, upload/send, file inventory, and
  pause/resume/cancel
- Elegoo Neptune 4-family guided setup over the existing Moonraker transport
- Elegoo Centauri Carbon and Carbon 2 beta local status/control integration;
  upload stays disabled until firmware exposes a safe confirmed file API
- Exponential reconnect backoff and circuit-breaking for repeated job-sync DB
  failures
- Capability-driven UI states for unsupported printer actions
- A provider conformance test suite every provider must pass, so a new printer
  backend is one small class rather than a new set of special cases
- Broader hardware validation notes for Moonraker, Bambu, PrusaLink, OctoPrint,
  and Neptune 4-family setups

Not in 0.9, added on demand: Prusa Connect (cloud, unlike the local PrusaLink
support that shipped here) and Elegoo models beyond the Neptune 4 and Centauri
Carbon families.

## 0.10 — Library Workflow Polish

Goal: make the vault better as a daily-use 3D print library.

- Better bulk editing for tags/categories and revision labels
- Saved filters or views for common searches, plus favorites/starred models
- Richer model/version comparison beyond the current G-code metadata compare
- More useful model detail pages for repeated reprints, including deeper print-history analytics
- Import/export paths for people migrating from folders or other tools

## 0.11 — Fleet and Scheduling

Goal: help small printer farms without turning PrintStash into a full slicer or
queue manager.

- Queue visibility with a provider-normalized job model
- Optional routing strategies: manual, default printer, least busy
- Printer maintenance windows, soft-drain mode, and a simple maintenance log

## 0.12 — Auth and Platform

Goal: fit cleanly into existing homelab infrastructure.

- OIDC / SSO login (Authentik, Authelia, and similar)
- Installable PWA with offline support (the mobile layout itself was refreshed in 0.8.x)
- Localization (i18n) scaffolding

## Later — Optional Cloud-Ready Adapters

Goal: support larger installs while keeping the default path self-hosted and
local-first.

- More complete Postgres deployment guidance
- S3/R2 lifecycle policy templates
- Multi-tenant/org routing only if there is real demand
- Cloud printer/provider adapters only when they do not compromise local-first use

## Not Planned Right Now

- A slicer
- Hard dependency on Postgres, Redis, S3, or external queues
- Replacing Moonraker/Bambu firmware as the source of truth for print execution
