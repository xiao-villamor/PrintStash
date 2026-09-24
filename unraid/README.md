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
   SQLite database, managed files, thumbnails, staging files, and backups.
4. Open the WebUI on a trusted local network and create the administrator
   account. The first person to register becomes the administrator; registration
   closes once an account exists. Do not expose first-run setup to the internet.

The template sets `VAULT_SETUP_MODE=trusted_network` for the initial registration
and `VAULT_RESTART_ENABLED=true` for Settings → Restart. The template uses
`--restart=unless-stopped` so Docker starts the whole container again when a
Settings restart exits the supervised app; a manual stop remains stopped.
It uses `PUID=99` and `PGID=100` for Unraid's usual `nobody:users` file
ownership. Set the numeric owner and group of your shares in the template's
advanced fields if they differ.
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
5. In the new template's advanced fields, set `PUID` and `PGID` to the old
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

The [unified Compose file](../docker-compose.unified.yml) is an alternative for
Docker Compose Manager. It uses the same image and first-run settings.
