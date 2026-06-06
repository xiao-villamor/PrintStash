# Known Limitations For 0.1

PrintStash 0.1 is an initial self-hosted release for local 3D printing asset
management. It is useful today, but it is deliberately not trying to be a full
manufacturing platform yet.

## Printer Providers

- Moonraker/Klipper is the primary supported provider for 0.1.
- Moonraker support includes live status, upload/send, optional start, remote
  file inventory sync, remote file start, pause/resume/cancel, and job history.
- Bambu LAN support is beta. It is limited to local status plus
  pause/resume/cancel controls.
- Bambu LAN upload, send-to-print, remote file inventory, and remote-file start
  are not implemented in 0.1.
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
- There is no default admin account. If setup cannot complete, fix setup rather
  than looking for bundled credentials.

## Data And Metadata

- Metadata extraction is best for common G-code emitted by OrcaSlicer,
  PrusaSlicer, Bambu Studio, Cura, and Klipper/Orca-style profiles.
- Slicer metadata comments vary by slicer and profile; missing fields are
  expected and should be reported with safe sample files.
- Metadata export is intentionally metadata-only. It does not include raw
  STL/3MF/G-code blobs, secrets, API keys, or printer credentials.
- Full backup/restore is available separately for moving or recovering an
  install.

## UI And Workflow

- The UI is functional and responsive, but repeated daily workflows still need
  polish.
- Bulk editing for tags/categories and revision labels is limited.
- Saved views and advanced comparison workflows are future work.
- The app is not a slicer, not a firmware replacement, and not a full queue
  manager.

## Out Of Scope For 0.1

- Public cloud service.
- CNC, laser, vinyl, PCB, or non-3D-printing adapters.
- Formal plugin system.
- Fleet scheduling strategies such as least-busy routing or maintenance windows.
- Advanced organization administration, approval workflows, and external
  business-system integrations.
- Cost analytics and advanced production traceability.
