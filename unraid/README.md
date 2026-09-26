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
- **NAS and existing folders:** index a mounted Unraid share in place, mirror
  folder structure as collections, detect changes, and optionally write new
  uploads or revisions back without overwriting existing files. Remote S3,
  WebDAV, and SFTP sources are read-only.
- **Private multi-user access:** accounts, per-collection view/edit/admin roles,
  API keys, audit logs, expiring read-only share links, and a recoverable trash
  workflow.
- **Printer-aware workflows:** stable Moonraker/Klipper support plus beta
  OctoPrint, PrusaLink, Bambu LAN, and supported Elegoo integrations. Available
  actions depend on each provider and can include live status, printer-file
  inventory, upload, start, controls, and job history.
- **Operations and cost:** measured Moonraker print duration and filament use,
  print statistics, optional Spoolman synchronization, Prometheus metrics, and
  local or remote storage and S3-compatible backup options. Local and generic
  S3 are stable; named S3, Nextcloud/WebDAV, and SFTP presets are beta (see
  [storage providers](../docs/storage-providers.md)).

See the [full capabilities](https://www.printstash.org/capabilities),
[printer compatibility matrix](https://www.printstash.org/compatibility), and
[documentation](https://www.printstash.org/docs/) before installation.

## Install with the Unraid template

The [PrintStash template](../templates/printstash.xml) now uses one container:
the published `ghcr.io/xiao-villamor/printstash:latest` image runs the web UI
and API together. No custom Docker network, second container, API port, JWT
secret, or command override is needed.

1. Install **PrintStash** using the `printstash.xml` template. The old
   **PrintStash-API** and **PrintStash-Frontend** templates are deprecated; do
   not select them for a new installation. Confirm the image is
   `ghcr.io/xiao-villamor/printstash:latest` before installing.
2. Keep **Network** on `bridge` and the **WebUI port** at `3000`, or choose another
   free host port.
3. Keep **Appdata** at `/mnt/user/appdata/printstash`, or choose a dedicated
   persistent folder. It maps to `/data` inside the container and holds the
   SQLite database, managed files, thumbnails, staging files, backups, and
   caches. Keep it one mapping: a separate mapping for `/data/files` puts the
   library on another mount than staging, and every import then copies its
   file instead of hard-linking it (Settings warns when that happens). Keep
   the share on one pool, as appdata is by default: a share spread across
   array disks can split staging and files the same way. Hard links are an
   optimization, not a requirement for file uploads: when SHFS refuses
   them, upload staging and local Vault publication use create-only copies.
   Keep enough free space for the additional copy. See
   [Hard-linked imports](../docs/deployment.md#hard-linked-imports).
4. Choose **First-run setup**, which decides how the first administrator is
   created:
   - `trusted_network` (the default): leave the administrator fields blank. The
     administrator registers in the browser, **only from your local network**:
     open the WebUI at `http://tower.local:3000` or the server's LAN IP.
     Registration through Tailscale, a VPN, a reverse proxy, Unraid Connect or a
     domain is refused. The first person to register becomes the administrator,
     and registration closes once an account exists. Do not expose first-run
     setup to the internet.
   - `environment`: fill in **Administrator username** (3 to 128 characters) and
     **Administrator password** (8 to 256 characters). PrintStash creates that
     administrator at first start; sign in with it and choose where files are
     stored. Choose this if you will first reach PrintStash through Tailscale, a
     VPN, a reverse proxy or Unraid Connect.
5. The mode and the fields must agree: `environment` needs both credentials, and
   `trusted_network` needs them blank. If they don't, setup stays closed and the
   WebUI names what to change. The fields are used once: editing them later does
   not change the account (reset a password under **Settings → Users**).

The template sets `VAULT_SETUP_MODE=trusted_network` for the initial registration
and `VAULT_RESTART_ENABLED=true` for Settings → Restart. The template uses
`--restart=unless-stopped` so Docker starts the whole container again when a
Settings restart exits the supervised app; a manual stop remains stopped.
It uses `PUID=99` and `PGID=100` for Unraid's usual `nobody:users` file
ownership. Set the numeric owner and group of your shares in the template's
**User ID** and **Group ID** fields if they differ.
The image's entrypoint creates and repairs managed data directories, runs
migrations, and generates a persistent signing secret when none was supplied.

**Keep existing model folders outside `/data`.** The managed `/data/files` path
is PrintStash's private store, not a folder to index. To import a user share,
add another Path mapping to the container, for example:

```text
Host path:      /mnt/user/3d-library
Container path: /mnt/library
Access:         Read/Write for enrollment
```

In PrintStash, add a **Library source** using `/mnt/library`, the container path.
After its root is verified, the mapping can be changed to read-only unless
writeback is needed. See the [Unraid library-source recipe](../docs/library-sources.md#unraid).
This separate mapping and the container's `PUID`/`PGID` must permit access to the
share. A host path such as `/mnt/user/3d-library` entered directly in the app is
not visible unless it is also mounted inside the container.

## Moving from the two-container template

1. Back up the existing database and appdata, including
   `/data/db/.printstash-secrets-key` if present. While the old API is still
   running, note the numeric owner of its database with
   `docker exec PrintStash-API stat -c '%u:%g' /data/db/printstash.sqlite`
   (replace `PrintStash-API` if you renamed the container). The old API image
   normally used `10001:10001`. If you set a custom `PUID`/`PGID` on the old
   container, keep those values instead. Also record any nonempty
   `VAULT_JWT_SECRET` in the old API's container settings.
2. Stop **PrintStash-API** and **PrintStash-Frontend**. Never run old and new
   containers against the same database or files at the same time.
3. The old template's default paths already sit under
   `/mnt/user/appdata/printstash/{files,thumbs,db,staging,backups}`. With those
   defaults, map the parent `/mnt/user/appdata/printstash` to `/data` in the new
   template. Confirm the five folders and the existing SQLite database are there
   before starting, so the new container does not create a fresh library.
4. If you customized any of the five old host paths, copy its contents into
   the corresponding subfolder of the new dedicated appdata parent first.
   Preserve the database's hidden signing-key file. Keep the backup until the
   library and files work through the new container.
5. In the new template's **User ID** and **Group ID** fields, set `PUID` and `PGID` to the old
   API's numeric data owner from step 1. Keeping that identity also keeps
   existing Library-source ownership markers readable. If the old API had a
   nonempty `VAULT_JWT_SECRET`, add it to the new template as a Variable with
   key `VAULT_JWT_SECRET` and the same value. Otherwise let the image reuse its
   persisted secret.
6. Recreate any separate Library-source mounts on the new container, start it,
   then sign in with the existing account. The image runs migrations at startup.
   Check that existing Library sources are still bound before importing files.
   If a source reports `root_marker_unreadable`, restore the old API's `PUID`
   and `PGID` and restart the container.

## Optional settings and troubleshooting

| Setting | Default | When to change it |
| --- | --- | --- |
| `VAULT_SETUP_MODE` | `trusted_network` | Set to `disabled` after first setup if desired; existing accounts already close registration. |
| `PUID` / `PGID` | `99` / `100` | For a fresh install, use the numeric user and group that can access your shares. On upgrade, keep the old API data owner (usually `10001:10001`). |
| `VAULT_MAX_UPLOAD_MB` | `512` | Raise for larger uploads, together with `NGINX_CLIENT_MAX_BODY_SIZE`. |
| `NGINX_CLIENT_MAX_BODY_SIZE` | `528m` | Keep above the API upload limit to allow request overhead. |

- **Setup says no trusted network:** verify the template's advanced
  `VAULT_SETUP_MODE` variable is `trusted_network`, then open the WebUI through
  the server's private IP address on your LAN. For a custom hostname, see
  [first-run addresses](../docs/first-run.md#access-addresses-and-proxies).
- **Storage check fails or Library source cannot be added:** verify that the
  appdata mount is writable, the separate share mount exists at the container
  path you entered, and `PUID`/`PGID` can access both. Do not point the managed
  storage path at the existing library. Check container logs for the failing
  path and restart after correcting the mapping.
- **A previous library looks empty:** stop the container and check the `/data`
  mapping against the old database location before uploading anything.
- **WebUI shows a 502:** the single container supervises both nginx and the API.
  Check its logs and health status; no `api` network alias is needed.

The default [Compose file](../docker-compose.yml) is an alternative for
Docker Compose Manager. It uses the same image and first-run settings.

## Uploads on `/mnt/user`

Unraid's `/mnt/user` shares pass through SHFS/FUSE. With hard-link support
unavailable, older PrintStash builds can fail an ordinary file upload with
`PermissionError: [Errno 1] Operation not permitted` at `os.link`, naming two
paths under `/data/staging/_incoming`. This happens **before** the file reaches
the selected Vault storage, so switching to WebDAV alone does not fix it.

The staging fallback is included in the **Unreleased** changes: use an image
built from a revision containing this fix until a release includes it. It
automatically copies when hard links are unavailable; no hard-link toggle is
needed. This covers both the older upload endpoint and the current browser's
resumable uploads, including native multipart completion and storage downloads.
Local Vault storage already has a create-only copy fallback. A
filesystem without reliable identity guarantees remains **Guarded** in storage
checks; successful uploads do not upgrade its safety tier.

There is no need to enable Unraid's global hard-link setting for this upload
path, bypass `/mnt/user`, or use Nextcloud as a workaround once the fix is
installed. Keep `PUID`/`PGID` matched to your share permissions: a genuinely
unwritable directory or full disk will still fail. Do not move an existing
installation between `/mnt/user` and a pool path while PrintStash is running.

## Keeping diagnostic logs

PrintStash sends application logs to container stdout/stderr. It does not
create a rotating application log under `/data`; Docker manages retention.
The WebUI audit log records user actions and is separate from diagnostic logs.

To bound Docker's log files, edit the container in Unraid's advanced view and
append these options to **Extra Parameters**, preserving the existing options:

```text
--log-driver=json-file --log-opt max-size=10m --log-opt max-file=5
```

These [Docker logging options](https://docs.docker.com/engine/logging/drivers/json-file/)
retain up to five files of about 10 MB each. Applying the settings recreates the
container; export useful logs **before** applying, updating, or removing it.
Rotation also removes the oldest entries, so export soon after a failure.
From the Unraid terminal (replace `PrintStash` with your container's name):

```bash
docker logs --timestamps --since 24h PrintStash > /mnt/user/appdata/printstash-diagnostics.log 2>&1
```

Save that file with the image tag/digest, the failed action, and the container
path mappings when reporting a problem. Review it for credentials and private
URLs before sharing. For retention across container replacements, forward
container logs to your existing log collector or export them to a persistent
share; Docker rotation alone does not provide that archive.

### Staging capability warnings

PrintStash probes `/data/staging` independently of the selected Vault provider.
On hardlinkless SHFS/FUSE shares, Settings explains that uploads use copies and
need more temporary space. Startup logs name each affected root once, and
`GET /api/v1/health/details` exposes the measurements under
`components.storage.diagnostics.staging`. The warning also appears with a remote
Vault: switching to WebDAV does not change the local staging filesystem.

These uploads no longer require a staging relocation or remote-provider
workaround. A disk or pool path supporting hard links can still reduce copying.
The staging warning does not change the selected Vault's safety tier.
