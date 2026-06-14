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
- Bambu LAN support is beta — local status plus pause/resume/cancel only.
- Bambu LAN upload, send-to-print, remote file inventory, and remote-file start
  are not implemented today.
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
- There is no default admin account. If setup cannot complete, fix setup rather
  than looking for bundled credentials.

## Data and metadata

- Metadata extraction is best for common G-code from OrcaSlicer, PrusaSlicer,
  Bambu Studio, Cura, and Klipper/Orca-style profiles.
- Slicer metadata comments vary by slicer and profile; missing fields are
  expected and worth reporting with safe sample files.
- Metadata export is metadata-only — no raw STL/3MF/G-code blobs, secrets, API
  keys, or printer credentials.
- Full backup/restore is available separately for moving or recovering an
  install.
- The G-code toolpath viewer is a browser-side visualization aid, not a
  slicer-grade simulator. It does not validate firmware macros, acceleration,
  pressure advance, or printer safety.

## UI and workflow

- The UI is functional and responsive, but repeated daily workflows still need
  polish.
- Bulk editing for tags/collections and revision labels is limited.
- Saved views and advanced comparison workflows are future work.
- Not a slicer, not a firmware replacement, not a full queue manager.

## Not current project goals

- Public cloud service.
- CNC, laser, vinyl, PCB, or non-3D-printing adapters.
- Formal plugin system.
- Fleet scheduling such as least-busy routing or maintenance windows.
- Approval workflows and external business-system integrations.
- Cost analytics and advanced production traceability.
