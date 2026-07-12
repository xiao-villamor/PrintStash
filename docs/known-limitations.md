# Known Limitations

PrintStash is an early self-hosted release for local 3D printing asset
management. It is useful today, but it is deliberately not trying to be a full
manufacturing platform.

## Printer Providers

- Moonraker/Klipper is the primary supported provider.
- Moonraker support includes live status, upload/send, optional start, remote
  file inventory sync, remote file start, pause/resume/cancel, and job history.
- Bambu LAN support is beta. It supports local status, upload of plain-text
  Vault G-code, explicit send-to-print, and pause/resume/cancel controls.
- Bambu LAN remote-file inventory, deletion, raw G-code controls, and measured
  filament consumption are not implemented.
- PrusaLink local FDM support is beta. Digest and legacy API-key authentication,
  status, upload/start, file inventory/deletion, and pause/resume/cancel are
  implemented; Prusa Connect cloud, SLA printers, raw G-code controls, and
  measured filament consumption are not.
- Elegoo Neptune 4, 4 Pro, 4 Plus, and 4 Max use Moonraker. Centauri Carbon and
  Carbon 2 have beta local status/control support, but no upload or inventory.
  Neptune 2/3, OrangeStorm, and SLA models are not covered.
- Provider behavior still needs more real-world hardware validation across
  printers, firmware versions, networks, and auth setups.

## Deployment

- Docker Compose is the recommended install path.
- SQLite and local disk are the default path and the best-tested path for home
  installs.
- Postgres, S3/R2 storage, MinIO, and cloud backup targets are optional and
  should be treated as larger-install paths.
- PrintStash is designed for trusted self-hosted networks. Do not expose it
  directly to the public internet without TLS, reverse proxy hardening, strong
  secrets, and network-level care.
- If you run a reverse proxy in front of the `api` service, set
  `FORWARDED_ALLOW_IPS` to the proxy's address so uvicorn trusts its
  `X-Forwarded-For` header. Left unset, the API only trusts `127.0.0.1`, so
  login rate limiting and audit-log IPs will show the proxy's address instead
  of the real client's. Only set it to `*` if the API port is unreachable
  except through that proxy — otherwise a direct connection can forge its own
  client IP and bypass rate limiting.
- There is no default admin account. If setup cannot complete, fix setup rather
  than looking for bundled credentials.
- Images are published for `linux/amd64` and `linux/arm64` (Raspberry Pi 4/5,
  ARM NAS, Apple-silicon VMs). On `linux/arm64`, STEP/STP preview and
  thumbnailing are unavailable because the OpenCASCADE tessellation dependency
  (`cascadio`) ships no Linux ARM wheel; STEP files still upload and store, they
  just don't get a generated mesh preview. Every other file type and feature is
  identical across architectures.

## Data And Metadata

- Metadata extraction is best for common G-code emitted by OrcaSlicer,
  PrusaSlicer, Bambu Studio, Cura, and Klipper/Orca-style profiles.
- Slicer metadata comments vary by slicer and profile; missing fields are
  expected and should be reported with safe sample files.
- Metadata export is intentionally metadata-only. It does not include raw
  STL/3MF/G-code blobs, secrets, API keys, or printer credentials.
- Full backup/restore is available separately for moving or recovering an
  install.
- The G-code toolpath viewer is a browser-side visualization aid. It is not a
  slicer-grade simulator and does not validate firmware-specific macros,
  acceleration, pressure advance, or printer safety.

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

## UI And Workflow

- The UI is functional and responsive, but repeated daily workflows still need
  polish.
- Bulk editing for tags/categories and revision labels is limited.
- Saved views and advanced comparison workflows are future work.
- The app is not a slicer, not a firmware replacement, and not a full queue
  manager.

## Not Current Project Goals

- CNC, laser, vinyl, PCB, or non-3D-printing adapters.
- Formal plugin system.
- Fleet scheduling strategies such as least-busy routing or maintenance windows.
- Advanced organization administration, approval workflows, and external
  business-system integrations.
- Cost analytics and advanced production traceability.
