---
title: Known limitations
description: Current rough edges and explicit non-goals.
---

PrintStash is an early self-hosted release for local 3D printing asset
management. It is useful today, but it is deliberately not trying to be a full
manufacturing platform.

## Printer providers

- Moonraker/Klipper is the primary supported provider, with live status,
  upload/send, optional start, remote file inventory sync, remote file start,
  pause/resume/cancel, and job history.
- Bambu LAN support is beta with local status, upload/start, and controls;
  remote inventory/deletion remains unavailable.
- PrusaLink local FDM support is beta; Prusa Connect cloud is not integrated.
- Elegoo Centauri Carbon and Carbon 2 status/controls are beta. Upload and file
  inventory are disabled because firmware exposes no safe confirmed API.
- Provider behavior still needs more real-world hardware validation across
  printers, firmware versions, networks, and auth setups.

## Deployment

- Docker Compose is the recommended install path.
- SQLite + local disk is the default and best-tested path for home installs.
- Postgres, S3/R2 storage, MinIO, and cloud backup targets are optional
  larger-install paths.
- PrintStash is designed for trusted self-hosted networks. Do not expose it
  directly to the public internet without TLS, reverse-proxy hardening, strong
  secrets, and network-level care.
- If you run a reverse proxy in front of the `api` service, set
  `FORWARDED_ALLOW_IPS` to the proxy's address so uvicorn trusts its
  `X-Forwarded-For` header — otherwise rate limiting and audit-log IPs show
  the proxy's address instead of the real client's. Only use `*` if the API
  port is unreachable except through that proxy.
- There is no default admin account. If setup cannot complete, fix setup rather
  than looking for bundled credentials.
- Images are published for `linux/amd64` and `linux/arm64`. On ARM, STEP/STP
  preview and thumbnailing are unavailable (the OpenCASCADE dependency has no
  Linux ARM wheel); STEP files still upload and store, they just don't get a
  generated mesh preview. All other file types and features are identical.

## Data and metadata

- Metadata extraction is best for common G-code from OrcaSlicer, PrusaSlicer,
  Bambu Studio, Cura, and Klipper/Orca-style profiles.
- Slicer metadata comments vary by slicer and profile; missing fields are
  expected and worth reporting with safe sample files.
- Metadata export is metadata-only: no raw STL/3MF/G-code blobs, secrets, API
  keys, or printer credentials.
- Full backup/restore is available separately for moving or recovering an
  install.
- The G-code toolpath viewer is a browser-side visualization aid, not a
  slicer-grade simulator. It does not validate firmware macros, acceleration,
  pressure advance, or printer safety.

## Notifications

- Notifications are opt-in, off by default, and superuser-managed.
- Channels cover print completed/failed/cancelled and printer-offline events,
  delivered to generic webhooks, Discord, Telegram, or ntfy.
- Channel secrets (webhook URLs, bot tokens, signing secrets) are stored
  unencrypted in the database, like the other configured secrets. Keep your
  install on a trusted network.
- Delivery is at-least-once: a retried or recovered send can arrive more than
  once, so receivers should de-duplicate on the `Idempotency-Key` header.
- The dispatcher is built for the default single-node deployment. It claims work
  safely against Postgres if you run multiple instances, but PrintStash is not
  otherwise designed or tested for horizontal scaling.
- Message formatting is fixed (no per-channel templates), the event set is the
  four above, and there is no separate "printer back online" event.
- An auto-disabled channel is not re-enabled automatically and does not raise a
  separate alert — check Settings → Notifications if alerts go quiet.

## UI and workflow

- The UI is functional and responsive, but repeated daily workflows still need
  polish.
- Bulk editing for tags/collections and revision labels is limited.
- Saved views and advanced comparison workflows are future work.
- Not a slicer, not a firmware replacement, not a full queue manager.

## Not current project goals

- CNC, laser, vinyl, PCB, or non-3D-printing adapters.
- Formal plugin system.
- Fleet scheduling such as least-busy routing or maintenance windows.
- Approval workflows and external business-system integrations.
- Cost analytics and advanced production traceability.
