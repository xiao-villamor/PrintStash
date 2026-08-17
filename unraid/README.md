# PrintStash on Unraid

PrintStash is an [open-source, self-hosted 3D print library and G-code vault](https://www.printstash.org)
for keeping models, sliced revisions, printer copies, and real print outcomes in
one searchable place on your Unraid server. It is a private alternative to
scattered download folders and cloud-only model lockers: your files remain on
your own storage, with no subscription or required external account.

## What PrintStash manages

- **Complete 3D print library:** STL, 3MF, OBJ, STEP/STP, G-code, and BGCODE,
  with collections, tags, search, thumbnails, content-hash duplicate detection,
  and in-browser mesh and G-code toolpath previews.
- **G-code revisions and outcomes:** attach multiple slices to one source model,
  compare parsed slicer settings, add notes, choose a recommended revision, and
  mark results as known good, failed, or needing testing.
- **Automatic print metadata:** extract slicer, printer profile, nozzle, layer
  height, material, temperatures, estimated duration, and filament use from
  OrcaSlicer, PrusaSlicer, Bambu Studio, and Cura output when available.
- **NAS and existing folders:** index shared volumes in place, mirror folder
  structure as collections, detect local changes, and write new uploads or
  revisions back without overwriting existing files.
- **Private multi-user access:** accounts, per-collection view/edit/admin roles,
  API keys, audit logs, expiring read-only share links, and a recoverable trash
  workflow.
- **Printer-aware workflows:** stable Moonraker/Klipper support plus beta
  OctoPrint, PrusaLink, Bambu LAN, and supported Elegoo integrations. Available
  actions depend on each provider and can include live status, printer-file
  inventory, upload, start, controls, and job history.
- **Operations and cost:** measured Moonraker print duration and filament use,
  print statistics, optional Spoolman synchronization, Prometheus metrics, and
  local or S3-compatible backup and storage options.

See the [full capabilities](https://www.printstash.org/capabilities),
[printer compatibility matrix](https://www.printstash.org/compatibility), and
[documentation](https://www.printstash.org/docs/) before installation.

## How the Unraid application is packaged

PrintStash runs as **two containers**:

| Container | Role | Port |
|-----------|------|------|
| **PrintStash-API** | Backend API + database | 8000 |
| **PrintStash-Frontend** | Web UI you open in the browser (nginx) | 3000 |

The frontend serves the app and proxies `/api/v1` (including WebSockets) to the
backend at the hostname **`api`**. Because of that, **both containers must share
a user-defined Docker network** that does name resolution — Unraid's built-in
`bridge` network does **not**, so the names won't resolve there.

---

## Install (Community Applications templates)

### 1. Create the network (one time)

On the Unraid terminal (or **Settings → Docker → add network**):

```bash
docker network create printstash
```

### 2. Install PrintStash-API **first**

From the `printstash-api` template:

- **Network:** `printstash`
- **JWT secret** (required): generate a long random string, e.g.
  ```bash
  openssl rand -hex 32
  ```
- Leave the volume paths at their defaults (`/mnt/user/appdata/printstash/...`)
  or point them at dedicated empty app-data directories.

> **Never map an existing model, NAS, or Nextcloud folder to `/data/files`.**
> This is PrintStash's private blob store, not an import path. Finish setup with
> the default dedicated directory, then add existing folders under **Settings →
> External Libraries** to index their files safely in place.

The template already:
- relies on the API image, which runs database migrations on every start
  (`alembic upgrade head`) from its entrypoint — no command override needed, and
- gives the container the network alias **`api`** so the frontend can reach it.

> Install the API **before** the frontend — the frontend expects `api` to
> already be resolvable on the `printstash` network.

### 3. Install PrintStash-Frontend

From the `printstash-frontend` template:

- **Network:** `printstash`
- Keep the **WebUI port** (default `3000`).
- If you raise the API's max upload size, match `NGINX_CLIENT_MAX_BODY_SIZE`
  here (e.g. `512m`).

### 4. Open the app and finish setup

Browse to `http://<server-ip>:3000` and complete the **first-run setup wizard**:

- create your admin account,
- choose **storage** — local disk (default) **or S3/R2** (bucket, endpoint, and
  keys are entered here in the wizard — *not* as container variables), and
- optionally configure **backups** (local and/or an S3 destination).

That's it — you're in your vault.

---

## Alternative: Docker Compose Manager plugin

PrintStash ships an official `docker-compose.yml` that already wires both
services, the network, and volumes. Migrations run from the image entrypoint on
every start, so there is no command to wire up. If you have the
**Compose Manager** plugin (from Community Applications), this is the simplest
path:

1. Install the *Docker Compose Manager* plugin.
2. Add a new stack and paste the repo's
   [`docker-compose.yml`](https://github.com/xiao-villamor/PrintStash/blob/main/docker-compose.yml).
3. Adjust volume paths to `/mnt/user/appdata/printstash/...` if you like. You do
   not need to set `VAULT_JWT_SECRET`; the API generates and persists one on first
   boot.
4. Compose up, then read the first-run setup token out of the API log
   (`docker logs printstash-api | grep "setup token"`) and open
   `http://<server-ip>:3000`.

---

## Configuration reference

Most settings are configured **in the app's setup wizard / Settings** and stored
in the database — including storage backend (local vs S3/R2) and backups. The
container variables are mainly bootstrap defaults:

| Variable | Container | Required | Notes |
|----------|-----------|----------|-------|
| `VAULT_JWT_SECRET` | API | – | Signs auth tokens. Generated and persisted on first boot if you leave it alone; set it (`openssl rand -hex 32`) only to own the value. Do not add it as an empty template variable, which is read as a deliberate choice and skips the generated secret. |
| `VAULT_SETUP_TOKEN` | API | – | First-run credential for the setup wizard. Unset means the API logs a random one per process while the vault is unconfigured; set it for a token that survives a container restart. |
| `VAULT_MAX_UPLOAD_MB` | API | – | Max upload size in MB (default `512`). |
| `NGINX_CLIENT_MAX_BODY_SIZE` | Frontend | – | Keep in sync with the above, e.g. `512m`. |
| `VAULT_BACKUP_RETENTION_DAYS` | API | – | `0` keeps backups forever. |
| `VAULT_ACCESS_TOKEN_EXPIRE_MINUTES` | API | – | JWT lifetime (default `60`). |
| `VAULT_LOG_LEVEL` | API | – | `DEBUG` / `INFO` / `WARNING` / `ERROR`. |
| `VAULT_METRICS_TOKEN` | API | – | If set, requires a bearer token to scrape `/metrics` (Prometheus). Leave empty to keep it open on your LAN. |

### Persistent paths (API container)

| Path | Holds |
|------|-------|
| `/data/files` | Private PrintStash model/G-code storage; must be a dedicated directory, never an existing library |
| `/data/thumbs` | Private generated-thumbnail storage; use a dedicated directory |
| `/data/db` | SQLite database |
| `/data/staging` | Temporary upload/import staging |
| `/data/backups` | Local backup archives |

---

## Troubleshooting

- **Frontend shows "connection refused" / 502 for `/api/v1`** — the API isn't
  reachable as `api` on the `printstash` network. Check that **both** containers
  are on the `printstash` network and that the API container has the
  `--network-alias api` extra parameter (the template sets this).
- **Stuck on a blank page or the network's default `bridge`** — recreate the
  containers on the user-defined `printstash` network; the default `bridge` has
  no DNS, so `api` can't resolve.
- **No login works on a fresh install** — there is no default admin account.
  Complete the first-run setup wizard to create one. If setup can't complete,
  fix setup (storage paths, JWT secret) rather than looking for built-in
  credentials.
- **Monitoring** — the API exposes Prometheus metrics at
  `http://<server-ip>:8000/metrics` for Grafana/Prometheus dashboards.
