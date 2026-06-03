# Changelog

## 1.0.0 - Stable Self-Hosted Release

PrintStash 1.0 is the first stable self-hosted release. It is focused on the
local-first library workflow: ingest STL/3MF/G-code, extract slicer metadata,
keep model revisions searchable, and integrate with Moonraker/Klipper printers.

### Highlights

- Docker Compose is the primary install path, with SQLite and local disk as the
  default storage stack.
- Alembic migrations provide a repeatable schema upgrade path.
- OrcaSlicer post-processing ingestion remains dependency-free and exits `0` on
  vault outages.
- G-code revisions support labels, outcome status, notes, recommended versions,
  and metadata comparison.
- Moonraker/Klipper is the stable 1.0 printer provider.
- Bambu LAN is available as beta status/control support only. Upload, send, start,
  and file inventory parity are intentionally not part of 1.0.
- Postgres, S3/R2 storage, cloud backups, and audit logs are optional adapters for
  larger self-hosted installs.

### Validation

- Backend unit/API suite covers ingestion, auth, migrations, parser fixtures,
  thumbnails, printer providers, and API hardening.
- Frontend CI runs typecheck, lint, and production build.
- Additional parser fixtures cover OrcaSlicer, PrusaSlicer, Bambu Studio, Cura,
  and a common Klipper/Orca profile.

### Upgrade Notes

Read [UPGRADE.md](./UPGRADE.md) before upgrading an existing install.
