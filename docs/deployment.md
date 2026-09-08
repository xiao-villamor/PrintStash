# Deployment and optional settings

For a new local installation, use [docker-compose.simple.yml](../docker-compose.simple.yml).
It starts the light API and web UI with SQLite and local storage. The short file
uses the images' built-in defaults; you only add settings you need. The light API
omits browser automation and STEP tessellation.

## Install

Install Docker with the Compose plugin, then:

```bash
mkdir -p printstash && cd printstash
curl -fsSL https://raw.githubusercontent.com/xiao-villamor/PrintStash/main/docker-compose.simple.yml -o docker-compose.yml
docker compose up -d
```

Open `http://localhost:3000` or `http://<server-ip>:3000`. Use a trusted network and create your administrator account in the browser.
The first person to complete registration owns the installation.
There is no default account. The API generates a new token on each start until
setup is complete. Database migrations run automatically at startup.

All commands below assume you saved the simple file as `docker-compose.yml`.
From a repository checkout, keep its original name and use
`docker compose -f docker-compose.simple.yml` instead of `docker compose`.

## One-container image

The optional [unified Compose file](../docker-compose.unified.yml) runs the full
API and nginx web UI in one container, with SQLite and local storage:

```bash
docker compose -f docker-compose.unified.yml up -d
```

Open `http://localhost:3000`. It uses
`ghcr.io/xiao-villamor/printstash:latest`, once published, and retains the same
five volume names as the other local Compose files. To switch an existing local
installation, back up first, stop the old stack without removing its volumes,
keep the same Compose project name, and carry over custom settings and mounts.
Do not run both stacks against the same data.

Put both API settings and `NGINX_CLIENT_MAX_BODY_SIZE` under
`services.printstash.environment`. `PUID`/`PGID`, automatic migrations and the
Settings restart button work through the existing API entrypoint. If either
service exits, the container exits and Compose restarts the application.

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
PRINTSTASH_IMAGE=printstash PRINTSTASH_VERSION=local docker compose -f docker-compose.unified.yml up -d
```

The existing **GHCR Release Images** workflow publishes native AMD64 and ARM64
images on release tags after CI passes. Run **Manual Docker Images** on the
default branch to publish `latest`. Both use the repository owner's GHCR namespace
and the built-in `GITHUB_TOKEN`; a separate registry password is unnecessary. Each architecture
runs the container tests before its digest is promoted to shared release tags.
Pull-request CI builds and tests without publishing.

### Container vulnerability reports

Grype scans every AMD64 and ARM64 image during pull-request and main-branch CI.
Publishing scans the immutable per-architecture digest before that digest can be
promoted into a multi-platform tag. A scanner, registry or vulnerability-database
failure stops the job; vulnerability matches remain report-only so an unfixed
upstream package does not silently make releases impossible.

Open the workflow run's **Artifacts** section and download the artifact named
`grype-ci-<image>-<architecture>` or
`grype-publish-<image>-<architecture>`. Each bundle is retained for 90 days and
contains:

- `summary.md` — severity totals and the number of matches with a known fix;
- `report.txt` — the complete human-readable Grype table;
- `report.json` — structured findings for automation and deeper triage;
- `report.sarif` — the report submitted to **Security → Code scanning** on
  trusted runs when code scanning is enabled for the repository. An unavailable
  Security-tab integration does not discard or block the downloadable reports.

Reports are snapshots against the vulnerability database available when the
workflow ran. Re-run CI when you need a current assessment of an unchanged
image.

On a fork, enable Actions before running the manual workflow. After the first
publish, open the `printstash` package's settings and change visibility to
**Public** if anonymous downloads are wanted. GHCR initially creates packages
as private even for public repositories; see
[GitHub's container registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

### Unraid, CasaOS and other container dashboards

Add the published image as a custom container with these settings:

| Setting | Value |
| --- | --- |
| Image | `ghcr.io/xiao-villamor/printstash:latest` (or your fork's namespace) |
| Network | Bridge |
| Web port | Host port of your choice → container TCP port `3000` |
| Persistent folder | A dedicated host folder → `/data` (read/write) |
| Environment | `VAULT_SETUP_MODE=trusted_network`, `VAULT_RESTART_ENABLED=true` |
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
port `3000` needs publishing. These are custom-container settings; this change
does not publish an Unraid Community Apps or CasaOS app-store listing.

## Add an optional setting

Add API settings under `services.api.environment` in your downloaded file.
Keep `VAULT_RESTART_ENABLED: "true"`, which lets Settings restart the supervised
API. For example, to retain remembered logins for seven days:

```yaml
services:
  api:
    environment:
      VAULT_RESTART_ENABLED: "true"
      VAULT_REMEMBER_ME_DAYS: "7"
```

This is a fragment to merge into the existing `api` service, **not a replacement
for the whole file**. Keep its image, volumes, and health check. Quote environment
values, especially booleans and numbers. After editing, apply the change:

```bash
docker compose up -d
```

Alternatively, save a fragment as `docker-compose.override.yml` beside the
downloaded `docker-compose.yml`; Compose loads it automatically. When using the
original filename in a Git checkout, select both files explicitly:

```bash
docker compose -f docker-compose.simple.yml -f docker-compose.override.yml up -d
```

A `.env` file supplies values for `${VARIABLE}` expressions in Compose. It does
**not** automatically pass every variable to the API. The simple file only
interpolates `PRINTSTASH_VERSION` and `PRINTSTASH_HTTP_PORT`. For another setting,
add it to `api.environment`, either directly as above or by reference:

```yaml
services:
  api:
    environment:
      VAULT_SETUP_ALLOWED_HOSTS: ${VAULT_SETUP_ALLOWED_HOSTS:-}
```

For a custom hostname, put `VAULT_SETUP_ALLOWED_HOSTS=printstash.example.net` in `.env`. Keep files containing
credentials private and outside version control. Do not copy the entire example
environment just to get started.

## Port and image version

These are the only optional variables already wired into the simple file:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PRINTSTASH_HTTP_PORT` | `3000` | Web port on the Docker host. |
| `PRINTSTASH_VERSION` | `latest` | Image tag, shared by the API and frontend. |

For example, put this in `.env` beside the downloaded Compose file:

```dotenv
PRINTSTASH_HTTP_PORT=8080
```

Run `docker compose up -d` and open `http://<server-ip>:8080`.
To pin a release, add `PRINTSTASH_VERSION=<published-image-tag>` using a tag from
[Releases](https://github.com/xiao-villamor/PrintStash/releases). Use the same tag
for both images; update it deliberately when upgrading.

## Data and host folders

The simple file persists all application state in named Docker volumes:

| Volume | Container path | Contents |
| --- | --- | --- |
| `printstash_data` | `/data/files` | Uploaded files |
| `printstash_thumbs` | `/data/thumbs` | Thumbnails |
| `printstash_db` | `/data/db` | SQLite database and generated credentials key |
| `printstash_staging` | `/data/staging` | Pending uploads and imports |
| `printstash_backups` | `/data/backups` | Local backup archives |

Docker prefixes these names with the Compose project name, normally the directory
name. Keep that directory/project name when updating so the app finds its data.
`docker compose down` preserves volumes; **`docker compose down -v` deletes them**.

For host folders, replace the API's `volumes` list with bind mounts and add the
host owner's numeric IDs to its existing `environment` mapping:

```yaml
services:
  api:
    environment:
      VAULT_RESTART_ENABLED: "true"
      PUID: "1000"
      PGID: "1000"
    volumes:
      - ./data/files:/data/files
      - ./data/thumbs:/data/thumbs
      - ./data/db:/data/db
      - ./data/staging:/data/staging
      - ./data/backups:/data/backups
```

Use `id -u` and `id -g` on the host to find the intended owner. Both IDs must be
positive; omitted IDs default to `10001:10001`. The entrypoint repairs ownership
before running migrations as the unprivileged user. Replacing named volumes with
empty host folders does not move existing data: back up and migrate it first.

To index an existing library folder, add a mount such as
`/path/to/library:/library:ro` to the API's existing volume list, then add
`/library` as a Library source in the app. Remove `:ro` only if you want the app
to write into that folder.

## Upload limits

The default per-file limit is 512 MiB. If you change it, set both the API limit
and the frontend's whole-request limit. Allow at least 16 MiB of multipart
headroom. For 1 GiB uploads:

```yaml
services:
  frontend:
    environment:
      NGINX_CLIENT_MAX_BODY_SIZE: "1040m"
  api:
    environment:
      VAULT_MAX_UPLOAD_MB: "1024"
```

Use `NGINX_CLIENT_MAX_BODY_SIZE` on the frontend, not on the API. An external
reverse proxy must also allow the larger request body. In the simple file,
`VAULT_MAX_REQUEST_MB` alone has no effect; that interpolation belongs to the
older Compose files.

## API settings reference

Add only the settings you need under `services.api.environment`.
These defaults apply when the setting is omitted.

### Login and sessions

| Variable | Default | Purpose |
| --- | --- | --- |
| `VAULT_SETUP_MODE` | `disabled` in the API; `trusted_network` in local Compose | Enables browser registration only for an unconfigured installation. Production Compose defaults to disabled. |
| `VAULT_SETUP_ALLOWED_HOSTS` | Empty | Extra comma-separated hostnames allowed for initial registration. Localhost, private addresses, `.local`, `.localhost`, and `.home.arpa` are already allowed. |
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
| `VAULT_MAX_UPLOAD_MB` | `512` | Per-file upload cap; also adjust the frontend as above. |
| `VAULT_PORTABLE_MANIFEST_MAX_MB` | `128` | Portable archive manifest cap. |
| `VAULT_STAGING_MAX_PENDING` | `32` | Pending staging capacity. |
| `VAULT_STAGING_MAX_ACTIVE_PER_USER` | `4` | Concurrent active staging operations per user. |
| `VAULT_STAGING_MAX_GB` | `4` | Staging disk budget. |
| `VAULT_STAGING_MIN_FREE_GB` | `1` | Minimum free disk space for staging. |
| `VAULT_INGEST_WORKER_COUNT` | `2` | Ingestion worker count. |
| `VAULT_MEDIA_WORKER_TIMEOUT_SECONDS` | `180` | Media worker timeout. |
| `VAULT_SQLITE_SYNCHRONOUS` | `NORMAL` | SQLite durability mode. |
| `VAULT_LOG_LEVEL` | `INFO` | API logging level. |
| `VAULT_BACKUP_RETENTION_DAYS` | `30` | Local backup retention in days. |
| `VAULT_RESTART_ENABLED` | `true` in simple Compose | Enables supervised restart from Settings; the app default outside Compose is `false`. |

Storage paths already match the persistent mounts. Leave `VAULT_DATA_DIR`,
`VAULT_THUMB_DIR`, `VAULT_DB_URL`, `VAULT_STAGING_DIR`, and `VAULT_BACKUP_DIR` at
their defaults unless you also adjust the mounts. A database path outside the
persistent volume can lose state on container replacement.

For remote storage, see [Storage providers](./storage-providers.md).
The full image includes the optional storage dependencies. PostgreSQL/S3 services
are not required for a local installation. For more environment settings, see
[.env.example](../.env.example); the application defaults are defined in
[Settings](../backend/app/core/config.py).

## HTTPS and reverse proxies

Use the simple deployment on a trusted network. For remote access, put it behind
your TLS reverse proxy and access controls. Edit the frontend port mapping in the
base file to bind it to localhost:

```yaml
ports:
  - "127.0.0.1:${PRINTSTASH_HTTP_PORT:-3000}:3000"
```

Replace the existing mapping; adding another port through an override may leave
the original public binding in place. Add `VAULT_SESSION_COOKIE_SECURE: "true"`
to the API for HTTPS. The API has no host port; the frontend proxies API requests
and WebSockets. If configuring `FORWARDED_ALLOW_IPS` on the API, trust only your
controlled proxy peers; see [Known limitations](./known-limitations.md).

The standalone [production Compose](../docker-compose.prod.yml) includes a
localhost binding and log rotation, and requires an explicitly configured
`VAULT_JWT_SECRET`. See [Security](../SECURITY.md).

## Stop, update, and troubleshoot

Run these in the install directory:

```bash
# Status and logs
docker compose ps
docker compose logs --tail=100 api

# Stop while preserving data
docker compose down

# Update to the selected image tag (latest unless pinned)
docker compose pull && docker compose up -d
```

Read [UPGRADE.md](../UPGRADE.md) and make a backup before updating. Check health
at `http://localhost:3000/api/v1/health` (use your chosen host/port).

## Which Compose file should I use?

| File | Purpose |
| --- | --- |
| **`docker-compose.simple.yml`** | **Recommended for a new local install.** Light API image, two services, minimal configuration. |
| `docker-compose.unified.yml` | Full API and web UI in one container, with native AMD64/ARM64 images. |
| `docker-compose.yml` | Existing configurable deployment with opt-in PostgreSQL and S3 profiles. |
| `docker-compose.light.yml` | Smaller API image without browser automation or STEP tessellation; exposes advanced variables. |
| `docker-compose.prod.yml` | Standalone configuration for a TLS reverse proxy, with localhost binding and log rotation. |
| `docker-compose.build.yml` | Source-build overlay for the original `docker-compose.yml`. |
| `docker-compose.light.build.yml` | Source-build overlay for the light file. |
| `docker-compose.manual-test.yml` | Maintainer testing stack. |
| `docker-compose.migrate-minio.yml` | Migration helper for existing MinIO installations. |

Existing installations can keep their current file. Do not start two stacks
against the same data. If switching to the simple file, back up first, stop the
old stack without removing volumes, keep the same Compose project name, and
carry over your custom mounts and settings. The simple file retains the five
local volume keys used by the existing stacks. Moving from PostgreSQL or remote
primary storage requires a separate data migration; copying this file is not one.
