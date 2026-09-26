# Deployment and optional settings

For a new installation, use [docker-compose.yml](../docker-compose.yml). It runs
PrintStash as one container — web UI and full API, with SQLite and local
storage. The short file uses the image's built-in defaults; you only add
settings you need. For every setting, PostgreSQL or S3, see
[Advanced deployment](#advanced-deployment-every-setting-postgresql-and-s3).

## Install

Install Docker with the Compose plugin, then:

```bash
mkdir -p printstash && cd printstash
curl -fsSL https://raw.githubusercontent.com/xiao-villamor/PrintStash/main/docker-compose.yml -o docker-compose.yml
docker compose up -d
```

Open `http://localhost:3000` or `http://<server-ip>:3000`. Use a trusted network and create your administrator account in the browser.
The first person to complete registration owns the installation.
There is no default account. The API generates a new token on each start until
setup is complete. Database migrations run automatically at startup.

From a repository checkout, `docker compose up -d` in the repository root uses
the same file. The commands below work in either place.

The container runs nginx and the API side by side. `PUID`/`PGID`, automatic
migrations and the Settings restart button work through the API entrypoint. If
either process exits, the container exits and Compose restarts it.

## Images from a fork or a local build

To use an image published from your fork, set these optional values in `.env`:

```dotenv
PRINTSTASH_IMAGE=ghcr.io/your-github-account/printstash
PRINTSTASH_VERSION=latest
PRINTSTASH_HTTP_PORT=3000
```

Build from a checkout with Docker Buildx (the API and frontend both come from
that checkout; no published PrintStash base image is required):

```bash
docker buildx bake -f docker-bake.hcl unified --load
PRINTSTASH_IMAGE=printstash PRINTSTASH_VERSION=local docker compose up -d
```

The existing **GHCR Release Images** workflow publishes native AMD64 and ARM64
images on release tags after CI passes. Run **Manual Docker Images** on the
default branch to publish `latest`. Both use the repository owner's GHCR namespace
and the built-in `GITHUB_TOKEN`; a separate registry password is unnecessary.
Pull-request CI validates the application without building container images.
The release workflow builds both architectures and runs the unified-image smoke
test before promoting their digests to shared tags.

On a fork, enable Actions before running the manual workflow. After the first
publish, open the `printstash` package's settings and change visibility to
**Public** if anonymous downloads are wanted. GHCR initially creates packages
as private even for public repositories; see
[GitHub's container registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

### Unraid, CasaOS and other container dashboards

For Unraid, use the [single-container template](../templates/printstash.xml)
and its [installation and migration guide](../unraid/README.md). Manifests for
Runtipi, Umbrel and CasaOS/ZimaOS are kept in [`catalogues/`](../catalogues/README.md)
and published to those stores with each release. On other container dashboards,
add the published image as a custom container with these settings:

| Setting | Value |
| --- | --- |
| Image | `ghcr.io/xiao-villamor/printstash:latest` (or your fork's namespace) |
| Icon URL | `https://raw.githubusercontent.com/xiao-villamor/PrintStash/main/frontend/public/logo.png` |
| Network | Bridge |
| Web port | Host port of your choice → container TCP port `3000` |
| Persistent folder | A dedicated host folder → `/data` (read/write) |
| Environment | `VAULT_RESTART_ENABLED=true`, and either `VAULT_SETUP_MODE=trusted_network` (register in the browser from the LAN) or `VAULT_SETUP_MODE=environment` with `VAULT_SETUP_ADMIN_USERNAME` and `VAULT_SETUP_ADMIN_PASSWORD` (create the administrator at first start) |
| Restart policy | `unless-stopped` |
| Stop timeout | `60` seconds |

The `/data` mount retains the SQLite database, uploaded files, thumbnails,
staging and backups across container updates. The image also includes the full
backend's optional storage adapters; SQLite remains the default database.
For host-folder ownership, set `PUID` and `PGID` to positive numeric IDs that
should own the data (both default to `10001`). Leave the image's entrypoint and
command at their defaults so ownership setup and migrations run before startup.

Open `http://<server-ip>:<host-port>` on a trusted network and complete the initial
administrator registration. The frontend proxies the API internally, so only
port `3000` needs publishing. The Unraid template supplies these settings
through one container. Other dashboards can use the custom-container settings
above.
The same PNG is available from your running frontend at `/logo.png`.

## Add an optional setting

MyMiniFactory account connection requires OAuth application credentials on the
API container: `VAULT_MMF_CLIENT_ID` and `VAULT_MMF_CLIENT_SECRET`. Register
`https://<your-public-origin>/api/v1/provider-connections/myminifactory/callback`
as the OAuth callback URL with MyMiniFactory, using the exact scheme and host
through which users open PrintStash. Restart the API after setting both values.
Without them, the connection endpoint returns `provider_not_configured` and
cannot start authorization. Cults uses the credentials entered by each user
and does not use these MyMiniFactory settings.

Add settings under `services.printstash.environment` in your downloaded file.
Keep `VAULT_RESTART_ENABLED: "true"`, which lets Settings restart the supervised
API. For example, to retain remembered logins for seven days:

```yaml
services:
  printstash:
    environment:
      VAULT_RESTART_ENABLED: "true"
      VAULT_REMEMBER_ME_DAYS: "7"
```

This is a fragment to merge into the existing `printstash` service, **not a
replacement for the whole file**. Keep its image and volumes. Quote environment
values, especially booleans and numbers. After editing, apply the change:

```bash
docker compose up -d
```

Alternatively, save a fragment as `docker-compose.override.yml` beside
`docker-compose.yml`; Compose loads it automatically with `docker compose up -d`.

A `.env` file supplies values for `${VARIABLE}` expressions in Compose. It does
**not** automatically pass every variable to the API. The default `docker-compose.yml` only
interpolates `PRINTSTASH_IMAGE`, `PRINTSTASH_VERSION` and `PRINTSTASH_HTTP_PORT`.
For another setting, add it to `printstash.environment`, either directly as above
or by reference:

```yaml
services:
  printstash:
    environment:
      VAULT_SETUP_ALLOWED_HOSTS: ${VAULT_SETUP_ALLOWED_HOSTS:-}
```

For a custom hostname, put `VAULT_SETUP_ALLOWED_HOSTS=printstash.example.net` in `.env`. Keep files containing
credentials private and outside version control. Do not copy the entire example
environment just to get started.

## Port and image version

These are the only optional variables already wired into the default `docker-compose.yml`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PRINTSTASH_HTTP_PORT` | `3000` | Web port on the Docker host. |
| `PRINTSTASH_VERSION` | `latest` | Image tag. |
| `PRINTSTASH_IMAGE` | `ghcr.io/xiao-villamor/printstash` | Image name, for a fork or a local build. |

For example, put this in `.env` beside the downloaded Compose file:

```dotenv
PRINTSTASH_HTTP_PORT=8080
```

Run `docker compose up -d` and open `http://<server-ip>:8080`.
To pin a release, add `PRINTSTASH_VERSION=<published-image-tag>` using a tag from
[Releases](https://github.com/xiao-villamor/PrintStash/releases); update it
deliberately when upgrading.

## Data and host folders

PrintStash keeps everything under one directory in the container, `/data`, and
the default `docker-compose.yml` mounts one named volume, `printstash`, there:

| Container path | Contents |
| --- | --- |
| `/data/db` | SQLite database and generated credentials key |
| `/data/files` | Uploaded files: the library |
| `/data/thumbs` | Thumbnails |
| `/data/staging` | Uploads and imports in progress |
| `/data/backups` | Local backup archives |
| `/data/artifact-cache` | Optional local cache for remote storage |
| `/data/ai-models` | Downloaded AI search models |

**Keep `/data` one mount**, so imports are hard-linked rather than copied. See
[Hard-linked imports](#hard-linked-imports) for which layouts keep that.

Docker prefixes the volume name with the Compose project name, normally the
directory name. Keep that directory/project name when updating so the app finds
its data. `docker compose down` preserves volumes; **`docker compose down -v`
deletes them**.

For a host folder, replace the volume with one bind mount and add the host
owner's numeric IDs to the existing `environment` mapping:

```yaml
services:
  printstash:
    environment:
      VAULT_RESTART_ENABLED: "true"
      PUID: "1000"
      PGID: "1000"
    volumes:
      - ./data:/data
```

Use `id -u` and `id -g` on the host to find the intended owner. Both IDs must be
positive; omitted IDs default to `10001:10001`. The entrypoint repairs ownership
before running migrations as the unprivileged user. Replacing the named volume
with an empty host folder does not move existing data: back up and migrate it
first.

### Moving one directory to another disk

Every path above is a child of `VAULT_DATA_ROOT`, and each can be moved on its
own, for example library files onto a large HDD while the database stays on an
SSD. Mount the other disk inside the container and point the matching variable
at it; `docker-compose.advanced.yml` shows each one, commented out.

| Variable | Default |
| --- | --- |
| `VAULT_DATA_ROOT` | `/data`. The parent of every path below; leave it alone in the container. |
| `VAULT_DATA_DIR` | `/data/files` |
| `VAULT_THUMB_DIR` | `/data/thumbs` |
| `VAULT_STAGING_DIR` | `/data/staging` |
| `VAULT_BACKUP_DIR` | `/data/backups` |
| `VAULT_ARTIFACT_CACHE_ROOT` | `/data/artifact-cache` |
| `VAULT_EMBEDDING_CACHE_DIR` | `/data/ai-models` |
| `VAULT_DB_URL` | `sqlite:////data/db/printstash.sqlite`, or a PostgreSQL URL |
| `VAULT_SECRETS_KEY_FILE` | `/data/db/.printstash-secrets-key` |

An empty value means the default. **Move `VAULT_DATA_DIR` and
`VAULT_STAGING_DIR` together**, onto the same mount; see below for why.

### Hard-linked imports

Every upload, URL import, library-transfer archive and printer capture is first
written to the staging directory. When the filesystem supports it, publication
into the local library uses a **hard link**: the staged file *becomes* the
library file. Publishing takes the same fraction of a millisecond at any size (a 2 GiB file that took about 7 s to copy
publishes in under 1 ms), and the file never occupies disk twice, not even
briefly.

A hard link works only **within one mount**. Linux refuses one between two
mounts even when both sit on the same disk. PrintStash then copies the file
instead. Nothing breaks, but every import gets slower as files grow and briefly
needs twice its size in free space. Ordinary file-upload staging also falls
back to an exclusive copy if its own filesystem refuses hard links (for example,
Unraid SHFS with hard-link support disabled). Existing files are never overwritten.
The fallback returns only after copying and syncing the bytes; interrupted copies
can leave an unreferenced partial staging file. See the
[Unraid upload and logging guide](../unraid/README.md#uploads-on-mntuser).

| Layout | Imports |
| --- | --- |
| One named volume or one host folder at `/data` (both Compose files) | Hard link |
| `VAULT_DATA_DIR` and `VAULT_STAGING_DIR` moved together to one other mount, e.g. both under `/mnt/hdd` | Hard link |
| **A second volume or host folder mapped onto a subfolder of `/data`**, e.g. `- hdd:/data/files` next to `- printstash:/data` | **Copy.** The subfolder is its own mount, even though no variable changed |
| `VAULT_DATA_DIR` on another mount while `VAULT_STAGING_DIR` stays on `/data`, or the reverse | **Copy** |
| One volume per subfolder (the layout of earlier releases) | **Copy** |
| A filesystem without hard links, such as some SMB/CIFS or FUSE mounts | **Copy** |
| S3, WebDAV or SFTP primary storage | Upload. There is no local file to link; this is not a misconfiguration |

Two more cases always copy by design: write-back into a mounted Library source
(that folder is its own mount), and thumbnails (they are generated, not
staged).

**How you are told.** PrintStash checks this at every start. When imports will
copy:

- Settings shows **"Imports are copied, not hard-linked"**, both on the overview
  and on the Storage section.
- For separate mounts, the log says `imports copy every staged file: staging (…) cannot hard-link into the library (…)`.
- For a filesystem without hard links, one startup warning names the affected
  root and explains that publication uses extra temporary space for copying.
  Staging is checked even when the Vault provider is remote.
- `GET /api/v1/health/details` reports
  `components.storage.diagnostics.staged_hardlink: false` for local imports.
  `components.storage.diagnostics.staging` records staging's `hardlink`,
  `exclusive_create`, `directory_fsync` and `fs_kind` capabilities for every
  provider; its warnings also appear in the storage capability response.

**Avoiding the extra copies** requires staging and the library to share a mount
that supports hard links, then restarting. A single SHFS/FUSE mount can still
require copying; uploads continue to work without relocating it:


- Remove a volume mapped onto a subfolder of `/data`, after copying its contents
  into the main volume the way [UPGRADE.md](../UPGRADE.md#unreleased-one-data-volume)
  moves the old volumes.
- Or, when files must live on another disk, point **both** `VAULT_DATA_DIR` and
  `VAULT_STAGING_DIR` at that disk's mount.

Also good to know:

- **Unraid:** keep the appdata share on one pool, as the template does by
  default. A share spread across array disks can put staging and files on
  different disks, and a link between disks falls back to a copy.
- **Nothing is left behind.** The staged name is removed once the link exists,
  so backups, `du` and file browsers see each file once.
- **Permissions:** a linked file is narrowed to `0600`, the same as a file
  PrintStash creates itself.
- **Free-space checks** still reserve room for a copy, so a nearly full disk is
  refused cleanly even when the link would have needed no space.

To index an existing library folder, add a mount such as
`/path/to/library:/library:ro` to the service's existing volume list, then add
`/library` as a Library source in the app. Remove `:ro` only if you want the app
to write into that folder.

## Upload limits

The default per-file limit is 512 MiB. If you change it, set both the API limit
and nginx's whole-request limit. Allow at least 16 MiB of multipart headroom.
For 1 GiB uploads:

```yaml
services:
  printstash:
    environment:
      NGINX_CLIENT_MAX_BODY_SIZE: "1040m"
      VAULT_MAX_UPLOAD_MB: "1024"
```

In `docker-compose.advanced.yml`, `NGINX_CLIENT_MAX_BODY_SIZE` belongs to the
`frontend` service instead. An external reverse proxy must also allow the larger
request body. In the default `docker-compose.yml`,
`VAULT_MAX_REQUEST_MB` alone has no effect; that interpolation belongs to
`docker-compose.advanced.yml`.

## API settings reference

Add only the settings you need under `services.printstash.environment` (or
`services.api.environment` in `docker-compose.advanced.yml`).
These defaults apply when the setting is omitted.

### Login and sessions

| Variable | Default | Purpose |
| --- | --- | --- |
| `VAULT_SETUP_MODE` | `disabled` in the API; `trusted_network` in Compose | How an unconfigured installation gets its first administrator. `trusted_network`: browser registration from the local network. `environment`: created at startup from `VAULT_SETUP_ADMIN_*`; the browser cannot register. `disabled`: no first-run path; use it for an internet-facing installation once set up. Contradicting combinations (credentials without `environment`, or `environment` without valid credentials) keep setup closed and the setup page names the variables to fix. |
| `VAULT_SETUP_ALLOWED_HOSTS` | Empty | Extra comma-separated hostnames allowed for initial registration. Localhost, private addresses, `.local`, `.localhost`, and `.home.arpa` are already allowed. Tailscale names (`*.ts.net`) and `100.x` addresses must be listed here. |
| `VAULT_SETUP_ADMIN_USERNAME` | Empty | With `VAULT_SETUP_MODE=environment`, creates the first administrator at startup when the installation has no owner. The administrator then signs in and chooses storage. Used once: it never changes an existing account. See [first use](first-run.md#an-administrator-from-the-deployment). |
| `VAULT_SETUP_ADMIN_PASSWORD` | Empty | Password for that administrator, at least 8 characters. Required in `environment` mode. Changing it later does not change the account's password. |
| `VAULT_SETUP_ADMIN_EMAIL` | Empty | Optional email for that administrator. |
| `VAULT_JWT_SECRET` | Generated and stored in the database | Manage your own signing secret; generate with `openssl rand -hex 32`. |
| `VAULT_SECRETS_KEY` | Generated key file in `/data/db` | External key for stored credentials. Preserve it with backups; changing it requires a planned key migration. |
| `VAULT_SESSION_COOKIE_SECURE` | `false` | Set `true` when accessed through HTTPS. |
| `VAULT_ACCESS_TOKEN_EXPIRE_MINUTES` | `60` | Ordinary login lifetime in minutes. |
| `VAULT_REMEMBER_ME_DAYS` | `2` | Remembered login lifetime in days. |

### OpenID Connect / SSO

Local login works without an identity provider. To enable OIDC, configure these
on the API for your provider:

| Variable | Default | Purpose |
| --- | --- | --- |
| `VAULT_OIDC_ENABLED` | `false` | Enable OIDC login. |
| `VAULT_OIDC_ISSUER_URL` | Empty | Provider issuer URL. |
| `VAULT_OIDC_CLIENT_ID` | Empty | Registered client ID. |
| `VAULT_OIDC_CLIENT_SECRET` | Empty | Registered client secret. |
| `VAULT_OIDC_REDIRECT_URI` | Empty | Explicit callback URL; otherwise derived from the request. |
| `VAULT_OIDC_SCOPES` | `openid profile email groups` | Requested scopes. |
| `VAULT_OIDC_USERNAME_CLAIM` | `preferred_username` | Username claim. |
| `VAULT_OIDC_GROUPS_CLAIM` | `groups` | Group membership claim. |
| `VAULT_OIDC_ADMIN_GROUPS` | `printstash-admins` | Groups granted administrator access. |
| `VAULT_OIDC_DISPLAY_NAME` | `Single sign-on` | Login button text. |
| `VAULT_OIDC_ALLOW_INSECURE_HTTP` | `false` | Allow HTTP provider endpoints for local testing only. |

### Imports, capacity, and diagnostics

| Variable | Default | Purpose |
| --- | --- | --- |
| `VAULT_MAX_UPLOAD_MB` | `512` | Per-file upload cap; also adjust `NGINX_CLIENT_MAX_BODY_SIZE` as above. |
| `VAULT_PORTABLE_MANIFEST_MAX_MB` | `128` | Portable archive manifest cap. |
| `VAULT_STAGING_MAX_PENDING` | `32` | Pending staging capacity. |
| `VAULT_STAGING_MAX_ACTIVE_PER_USER` | `4` | Concurrent active staging operations per user. |
| `VAULT_STAGING_MAX_GB` | `4` | Staging disk budget. |
| `VAULT_STAGING_MIN_FREE_GB` | `1` | Minimum free disk space for staging. |
| `VAULT_MEDIA_WORKER_TIMEOUT_SECONDS` | `180` | Media worker timeout. |
| `VAULT_SQLITE_SYNCHRONOUS` | `NORMAL` | SQLite durability mode. |
| `VAULT_LOG_LEVEL` | `INFO` | API logging level. |
| `VAULT_BACKUP_RETENTION_DAYS` | `30` | Local backup retention in days. |
| `VAULT_RESTART_ENABLED` | `true` in Compose | Enables supervised restart from Settings; the app default outside Compose is `false`. |

Storage paths all default under the `/data` volume; see
[Data and host folders](#data-and-host-folders) before moving one. A database
path outside a persistent mount loses state on container replacement.

### Background work

| Variable | Default | Purpose |
| --- | --- | --- |
| `VAULT_PROCESS_ROLE` | `all` | `all` runs HTTP and every background Job; `api` is the one HTTP process of a deployment with [workers](#background-work-and-workers). |
| `VAULT_API_RUNS_JOBS` | `true` | With `api`, whether the API also runs Jobs; `false` leaves them to the workers. |
| `VAULT_SHARED_STORAGE` | `false` | Declares that every process mounts the same volumes; required with workers. |
| `VAULT_MAX_RENDER_JOBS` | `1` | Mesh renders and local AI inference at once, per process; raise it on hosts with spare RAM. |
| `VAULT_JOBS_INGEST_CONCURRENCY` | `2` | Uploads and imports committed at once. |

Settings → Background work overrides each kind of work's concurrency at runtime,
for every process. The other `VAULT_JOBS_*` defaults are in
[Background work](architecture/background-work.md#configuration).

For remote storage, see [Storage providers](./storage-providers.md).
The full image includes the optional storage dependencies. PostgreSQL/S3 services
are not required for a local installation. Every setting above is already wired,
with its default, in [docker-compose.advanced.yml](../docker-compose.advanced.yml)
(see [below](#advanced-deployment-every-setting-postgresql-and-s3)). For more
environment settings, see [.env.example](../.env.example); the application
defaults are defined in
[Settings](../backend/app/core/config.py).

## HTTPS and reverse proxies

Use the default deployment on a trusted network. For remote access, put it behind
your TLS reverse proxy and access controls. Edit the port mapping in
`docker-compose.yml` to bind it to localhost:

```yaml
ports:
  - "127.0.0.1:${PRINTSTASH_HTTP_PORT:-3000}:3000"
```

Replace the existing mapping; adding another port through an override may leave
the original public binding in place. Add `VAULT_SESSION_COOKIE_SECURE: "true"`
for HTTPS. Only the web port is published; nginx inside the container proxies API
requests and WebSockets. If configuring `FORWARDED_ALLOW_IPS`, trust only your
controlled proxy peers; see [Known limitations](./known-limitations.md).

For an internet-facing installation, also set your own `VAULT_JWT_SECRET`
(`openssl rand -hex 32`) and `VAULT_SETUP_MODE: "disabled"` once setup is
complete. [docker-compose.advanced.yml](../docker-compose.advanced.yml) has all of
these wired, rotates container logs, and shows the localhost binding in a
comment. See [Security](../SECURITY.md).

## Stop, update, and troubleshoot

Run these in the install directory:

```bash
# Status and logs
docker compose ps
docker compose logs --tail=100 printstash

# Stop while preserving data
docker compose down

# Update to the selected image tag (latest unless pinned)
docker compose pull && docker compose up -d
```

Read [UPGRADE.md](../UPGRADE.md) and make a backup before updating. Check health
at `http://localhost:3000/api/v1/health` (use your chosen host/port).

## Advanced deployment: every setting, PostgreSQL and S3

[docker-compose.advanced.yml](../docker-compose.advanced.yml) is the reference
deployment. It runs the web UI and API as separate containers (the API image can
be swapped for the smaller `printstash-api-lite`), wires every setting on this page to a `${VARIABLE}` with its default, rotates
container logs, and has opt-in PostgreSQL and SeaweedFS (S3) services. Put your
values in a `.env` file next to it (start from [.env.example](../.env.example)):

```bash
docker compose -f docker-compose.advanced.yml up -d
# Optional services, internal-only:
docker compose -f docker-compose.advanced.yml --profile postgres --profile s3 up -d
```

Include `-f docker-compose.advanced.yml` in every later Compose command, or save
it as `docker-compose.yml` in its own install directory. To build from a checkout
instead of pulling images, uncomment its two `build:` blocks and add `--build`.

Existing installations keep their data when switching between
`docker-compose.yml` and `docker-compose.advanced.yml`: both mount the same
`printstash` volume at `/data`. Back up first, stop the old stack without removing volumes,
keep the same Compose project name, and carry over custom settings and mounts.
Do not run two stacks against the same data. Moving from PostgreSQL or remote
primary storage requires a separate data migration; switching files is not one.

## Background work and workers

Imports, previews, metadata, backups, scans, search indexing and notifications
run as background Jobs. By default the API process runs them itself
(`VAULT_PROCESS_ROLE=all`), which is right for almost every installation, and
what both Compose files do without any setting. Settings → Background work
shows what is running and lets an administrator change how many Jobs of each
kind run at once.

| Topology | How | Requires |
| --- | --- | --- |
| One process (default) | Either Compose file, unchanged | SQLite or PostgreSQL |
| API plus workers | Advanced file, `--profile workers` | PostgreSQL and shared volumes |
| API without jobs | The same, with `VAULT_API_RUNS_JOBS=false` | PostgreSQL and shared volumes |

To add workers, uncomment the PostgreSQL `VAULT_DB_URL` in
`docker-compose.advanced.yml` (the API and the workers share its settings),
then set in `.env`:

```bash
VAULT_PROCESS_ROLE=api
VAULT_SHARED_STORAGE=true
# Optional: leave all background work to the workers.
VAULT_API_RUNS_JOBS=false
# How many worker containers to run (default 2).
PRINTSTASH_WORKERS=2
```

```bash
docker compose -f docker-compose.advanced.yml --profile postgres --profile workers up -d
```

A worker runs the API image with `VAULT_PROCESS_ROLE=worker` and the command
`/app/.venv/bin/python -m app.worker`. It serves no HTTP, never migrates (it
waits for the API to), and stops cleanly on `SIGTERM`. Every process mounts the
same `/data` volume, so the worker that commits an upload reads what the API
staged, and local AI Search models (`/data/ai-models`) are there for workers
embedding while indexing and for a download wherever its Job runs. A worker
refuses to start on SQLite or without `VAULT_SHARED_STORAGE=true`, saying why.
Keep exactly one API container per vault.

The engine keeps its own state beside the vault database (SQLite) or in the
`dbos` schema (PostgreSQL). It is disposable, not part of a backup, and rebuilt
after a restore. Tuning settings are listed in
[Background work](architecture/background-work.md#configuration).

## Other Compose files

| File | Purpose |
| --- | --- |
| **`docker-compose.yml`** | **Recommended.** One container with web UI and full API, SQLite, no configuration. |
| `docker-compose.advanced.yml` | Every setting wired; separate web UI and API containers; opt-in PostgreSQL, S3 and [workers](#background-work-and-workers). |
| `deploy/manual-testing/compose.yml` | Maintainer release-testing stack. |
| `deploy/minio-migration/compose.yml` | One-release helper for old bundled MinIO data; see [MinIO migration](./minio-migration.md). |
