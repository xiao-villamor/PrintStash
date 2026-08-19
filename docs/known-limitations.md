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
  Carbon 2 have beta local status/control support and beta chunked HTTP
  upload, but no file inventory, deletion, or print-history import. Neptune
  2/3, OrangeStorm, and SLA models are not covered.
- Provider behavior still needs more real-world hardware validation across
  printers, firmware versions, networks, and auth setups. In particular, the
  0.11.0 protocol corrections — the Moonraker `server.connection.identify`
  handshake, Bambu LAN MQTT report-confirmed commands and status reads, and
  the PrusaLink v1 file/job/start alignment — are verified against mocked
  transports and emulators only; no real-hardware Validation Log rows exist
  yet (see `docs/provider-support.md`). The 0.11.3 Elegoo CC1 upload path has a
  community report of a successful upload-only smoke through an isolated
  PrintStash instance, including status/model detection, Vault retrieval,
  capability gating, and independent confirmation of the remote file. Active
  print controls and reconnect-while-paused remain untested because that
  printer had a separate filament-runout hardware fault; the CC2 upload path
  is unconfirmed entirely.

## Deployment

- Docker Compose is the recommended install path.
- SQLite and local disk are the default path and the best-tested path for home
  installs.
- Postgres, S3/R2-compatible storage, SeaweedFS, and cloud backup targets are optional and
  should be treated as larger-install paths.
- The built-in database backup and restore operation supports file-backed
  SQLite only. PostgreSQL installations must use operator-managed `pg_dump`
  and restore procedures; the backup API exposes this capability explicitly
  and rejects unsupported database operations without modifying data.
- One API process is the supported topology. Do not pass `--workers` greater
  than one or run multiple API replicas against the same vault: scheduling,
  rate limits, session coordination, and background registries are deliberately
  process-local. Startup claims a vault lock and fails fast if another API
  process is already active.
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
  ARM NAS, Apple-silicon VMs). Cascadio 0.1.1 provides OpenCASCADE wheels for
  both targets, so the full image can preview and thumbnail STEP/STP files on
  either architecture. CI now tessellates a real STEP fixture in an ARM64 image
  under QEMU. Native Raspberry Pi and representative 1 GB hardware validation
  are still outstanding, so this is runtime compatibility evidence rather than
  a physical-device performance claim.
- STEP tessellation runs in a disposable child process. Its resident-memory
  ceiling uses the existing cgroup-aware mesh memory budget and it has a 90 s
  timeout; an over-budget or overly complex file is stored without geometry or
  a generated preview instead of risking the API process. The generic 200 MiB
  mesh input cap still applies first and is format-blind. Operators can adjust
  the deadline with `VAULT_MESH_STEP_TIMEOUT_SECONDS`; memory continues to use
  `VAULT_MESH_MEMORY_BUDGET_FRACTION` rather than a STEP-only byte guess.
- The lite image intentionally omits browser-assisted imports and STEP/STP
  tessellation. It still includes NumPy, Pillow, and Trimesh, so STL/OBJ/3MF
  thumbnail generation does not depend on Chromium, OpenGL, or Cascadio.

## Data And Metadata

- Metadata extraction is best for common G-code emitted by OrcaSlicer,
  PrusaSlicer, Bambu Studio, Cura, and Klipper/Orca-style profiles.
- Slicer metadata comments vary by slicer and profile; missing fields are
  expected and should be reported with safe sample files.
- Externally-started Bambu jobs preserve only fields supplied by printer MQTT.
  Exact G-code/project recovery is best-effort while the FTPS cache entry is
  available; cloud-only, evicted, or ambiguous jobs remain metadata-only.
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
- The dispatcher is built for the supported single-process deployment. Its
  database claim remains defensive, but horizontal API scaling is not supported.
- Message formatting is fixed (no per-channel templates), the event set is the
  four above, and there is no separate "printer back online" event.
- An auto-disabled channel is not re-enabled automatically and does not raise a
  separate alert — check Settings → Notifications if alerts go quiet.

## UI And Workflow

- The UI is functional and responsive, but repeated daily workflows still need
  polish.
- v0.10.0 added broader bulk editing, saved views, and richer Artifact
  comparison; earlier releases had limited bulk editing and no saved views.
- The app is not a slicer, not a firmware replacement, and not a full queue
  manager.

## Vault Audit And Recovery

- Audits are diagnostic and read-only. Repairs are deliberately limited to
  regenerating thumbnails, reparsing Metadata from readable Artifacts, and
  restoring the recommended Revision invariant. Missing primary blobs, hash
  mismatches, unavailable external roots, and unowned objects require manual
  recovery and are never deleted automatically.
- Full audit hashes owned primary blobs and can be expensive on remote/S3
  storage. Cancellation occurs between objects rather than during one object
  stream.
- Backup verification validates archive safety, manifest/database membership,
  declared member sizes, and manifest compatibility. It does not restore an
  individual blob or prove that every application-level database invariant is
  healthy.

## Pending Imports And Facets

- Authenticated source sites may still require a fresh browser session through
  the existing final import flow. Pending Imports and the browser helper never
  persist or copy source-site cookies.
- URL captures are limited to existing Printables, MakerWorld, Thingiverse,
  direct-file, and safe archive resolvers. The browser helper does no scraping.
- Facet counts describe Models in the currently filtered, accessible scope.
  They are not self-excluding counts; selecting a value can therefore narrow
  values in other groups.

## Fleet Scheduling

- Fleet scheduling is limited to Vault-backed plain-text G-code. Users with a
  printer's `print` role can manually queue work to that printer; automatic
  default/least-busy routing remains administrator-managed because it can select
  another printer. PrintStash does not slice, modify, or validate G-code.
- Automatic dispatch requires a provider that advertises both upload and start
  capabilities. Status/control-only integrations remain visible but are not
  eligible routing targets.
- Maintenance windows are one-off. Recurring maintenance, deadlines,
  dependency graphs, production orders, and automatic print-failure recovery
  are not implemented.
- Soft drain blocks new dispatches but deliberately never pauses or cancels an
  active print.
- The default dispatcher uses the local database and asyncio. No Redis or
  external queue is required; horizontal multi-node scheduling is not a 0.11
  target.
- Active jobs are always returned by the fleet queue API. Finished history is
  limited to 20 rows by default and can be paged in bounded batches up to 100.
- If the process stops after provider I/O begins but before its outcome is
  persisted, PrintStash reports `dispatch_outcome_unknown` and disables
  automatic retry. Check the physical printer before enqueueing that work again.
- Scheduler health, last-tick time, blocked jobs, and dispatch outcomes are
  exposed through authenticated health details and Prometheus metrics.

## Auth And Platform

- Printer roles are ordered: `view`, `print`, `control`, and `admin`. Grants are
  per user and printer; group-wide grants, custom capability sets, time-limited
  grants, and OIDC group-to-printer mapping are not implemented. API keys use
  their owning user's current grants.

- OIDC is optional and configured under Settings → SSO or with environment
  variables. Saved client secrets are encrypted at rest. Local login remains
  available; PrintStash does not synchronize IdP passwords or provide an IdP.
- JIT provisioning maps only configured group names to superuser access. It does
  not import arbitrary provider roles or organizations.
- Offline PWA support caches the application shell and previously fetched static
  assets. API data, printer state, uploads, and library mutations always require
  a live connection to the self-hosted server and are never cached by the service
  worker.
- English and Spanish establish the localization seam. Remaining feature-heavy
  screens will be extracted incrementally; untranslated strings fall back to
  English.

## Not Current Project Goals

- CNC, laser, vinyl, PCB, or non-3D-printing adapters.
- Formal plugin system.
- Advanced production scheduling beyond manual/default/least-busy routing and
  one-off maintenance windows.
- Advanced organization administration, approval workflows, and external
  business-system integrations.
- Cost analytics and advanced production traceability.
## Material-aware routing

- Material comparison trims whitespace and ignores case, but deliberately does
  not guess aliases such as `PLA+` and `PLA`.
- Bambu AMS state depends on the fields exposed by the installed firmware.
  Moonraker's active Spoolman spool is tracked configuration, not physical
  detection. Other providers require manual feed state.
- Color differences are advisory. PrintStash does not slice files, purchase
  material, or account for consumption across multiple spools in one print.
- Multi-material G-code without a complete tool-to-feed mapping remains
  `unknown` rather than being declared compatible or mismatched.
