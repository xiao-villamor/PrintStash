# App-store catalogues

Source manifests for the stores that list PrintStash. Each one runs the
single-container image described by the root [`docker-compose.yml`](../docker-compose.yml)
and is submitted to that store's own repository. The Unraid template lives in
[`templates/printstash.xml`](../templates/printstash.xml), which Community
Applications reads directly.

| Store | Files | First administrator |
| --- | --- | --- |
| Unraid | `templates/printstash.xml` | **First-run setup** dropdown: `trusted_network` (default; register in the browser, fields blank) or `environment` (fields required) |
| Runtipi | `runtipi/printstash/` | `environment`, with required install-form fields, so the owner exists before the app serves a request |
| Umbrel | `umbrel/printstash/` | `environment`, with Umbrel's per-app credentials (`defaultUsername`, `deterministicPassword`) shown on the app's page |
| CasaOS / ZimaOS | `casaos/PrintStash/` | `trusted_network` by default; set `VAULT_SETUP_MODE=environment` and fill the fields to provision instead |
| Cosmos Cloud | `cosmos/PrintStash/` | `environment`, from required install-dialog fields: Cosmos terminates TLS and proxies every app, so browser registration through it is refused |

All of them map the store's form or credentials to `VAULT_SETUP_ADMIN_*`, which
creates the administrator once at first start (see
[first use](../docs/first-run.md#an-administrator-from-the-deployment)). The
administrator then signs in and chooses storage in the browser.

`VAULT_SETUP_MODE` names which door each store uses, and PrintStash checks the mode
and the credentials together (see
[first use](../docs/first-run.md#an-administrator-from-the-deployment)). Where
the store always supplies the credentials (Runtipi, Umbrel, Cosmos) the mode is
`environment`: the owner exists before the first request and the browser has no
door. Where the fields may be blank (CasaOS, Unraid) the default is
`trusted_network`, browser registration from the local network; switching to
`environment` makes the fields required. A combination that doesn't fit keeps
setup closed and the setup page names what to change.

`backend/tests/repo/test_catalogue_manifests.py` checks that every manifest runs
the unified image at the current app version, persists `/data`, uses the setup
mode above, never passes `VAULT_JWT_SECRET`, and wires the administrator
settings.

## Releasing: submitting and updating each store

The release commit bumps every manifest to the new version; the version test fails
until it does. Once the tag is published and the image
`ghcr.io/xiao-villamor/printstash:X.Y.Z` exists for `amd64` and `arm64`, each store
is updated as below. The release procedure in
`.agents/skills/printstash/references/release.md` runs these steps. Opening a pull
request in another project's repository is public, so the release confirms the list
with the maintainer first.

Check the image before touching any store:

```bash
docker buildx imagetools inspect ghcr.io/xiao-villamor/printstash:X.Y.Z
```

The digest it prints (`sha256:…`) is what Umbrel pins.

### Unraid

Nothing to submit. Community Applications re-reads `templates/printstash.xml` from
`main`, and the template deliberately tracks `:latest`, the Community Applications
convention, so it is the one manifest the version test does not pin.

### Umbrel: pull request to `getumbrel/umbrel-apps`

That repository ships agent skills for exactly this: `umbrel-package-app` (first
submission), `umbrel-update-app` (every release) and `umbrel-test-app`. Follow them
in a checkout of the fork; they are the store's own contract. The store keeps one
directory per app at its repository root, so the package is `printstash/`, not
`apps/printstash/`.

1. Fork and branch: `gh repo fork getumbrel/umbrel-apps --clone`, then
   `git switch -c printstash-X.Y.Z`.
2. Copy `catalogues/umbrel/printstash/` to `printstash/`, `data/.gitkeep` included:
   compose mounts `${APP_DATA_DIR}/data`, and the source directory has to exist in
   the package or the first start fails.
3. Pin the image with its tag and its multi-arch index digest together:
   `image: ghcr.io/xiao-villamor/printstash:X.Y.Z@sha256:…`. Read the digest with
   `docker buildx imagetools inspect ghcr.io/xiao-villamor/printstash:X.Y.Z` and
   confirm it lists `linux/amd64` and `linux/arm64`. The linter rejects a tag
   without a digest, and the store's CI resolves the tag and fails when the digest
   it gets is not the one pinned.
4. Keep the fields the store validates: `storage: {dataRoot: data}`, which lets
   umbrelOS 2.0 move the library to another drive and cannot be removed once
   shipped; `manifestVersion: 1`; and a `port` unique across the store (`3378` is
   free as of 2026-10-21, and the linter checks every package for a collision).
5. Keep Umbrel's login in front of the app. `app_proxy` sets
   `PROXY_AUTH_WHITELIST: "/api/*"` and nothing else: slicers and print hosts call
   `/api/*` with PrintStash's own tokens and cannot send an Umbrel cookie, while the
   UI stays behind the Umbrel login, 2FA included. Do not set
   `PROXY_AUTH_ADD: "false"`; the store reserves that for apps whose main use breaks
   behind its login, and `PROXY_AUTH_WHITELIST` has no effect alongside it.
6. Run the container as Umbrel's data owner: the compose file sets `PUID` and
   `PGID` to `1000`, the UID/GID umbrelOS creates app data with. The image repairs
   ownership at start and then drops to that identity, so the library on disk stays
   owned by the user Umbrel uses.
7. `restart: unless-stopped` stands where Umbrel apps usually use `on-failure`: a
   restart requested from Settings is a graceful exit that can return status 0,
   which `on-failure` would not relaunch. Say so in the PR body, and leave
   `stop_grace_period: 60s` alone; it is what lets a running job finish.
8. **First submission only:** `releaseNotes: ""`, `gallery: []` and no `icon` (the
   Umbrel team creates the official assets before merge), set `submitter`, and set
   `submission` to the PR URL once it exists. The linter rejects an empty
   `submission`, so the first push fails `lint:apps` until that commit lands.
9. **Every release:** bump `version`, update the image tag and digest together, and
   write `releaseNotes` from the CHANGELOG entry. YAML folds single newlines into
   spaces, so keep two blank lines between paragraphs and a blank line before and
   between list items.
10. Lint before opening the PR: `npm run lint:apps -- printstash --check-images`
    checks the manifest, the port, image pinning, public pullability, multi-arch
    support and the `app_proxy` wiring, and verifies the digest against the
    registry; then `git diff --check`.
11. Test with `umbrel-test-app` (install on umbrelOS, sign in with the credentials
    Umbrel shows, choose storage, upload a model), then open the PR stating the app
    version, upstream URL, image source, testing performed, default credentials and
    the restart and auth decisions. Put screenshots and the logo in the PR body;
    never commit gallery or icon assets.

### CasaOS / ZimaOS: pull request to `IceWhaleTech/CasaOS-AppStore`

The store source is the v2 protocol: one `Apps/<App>/docker-compose.yml` holding the
Compose file plus a top-level `x-casaos` block, and the assets that block names
beside it. `catalogues/casaos/PrintStash/` is exactly that directory, so a
submission and every update are a copy of it.

1. Fork and branch: `gh repo fork IceWhaleTech/CasaOS-AppStore --clone`, then
   `git switch -c printstash-X.Y.Z`.
2. Copy the whole directory, assets included:
   `cp -r catalogues/casaos/PrintStash <checkout>/Apps/PrintStash`. The icons,
   thumbnail and screenshots must travel with it: the store's build resolves each
   `x-casaos` asset URL by filename inside the app directory, so a URL without
   its file builds a listing with no icon.
3. **Every release:** bump the image tag, `x-casaos.version`,
   `x-casaos.update_at` (`YYYY-MM-DD`) and `x-casaos.release_notes` in `en_US`
   and `es_ES`. Refresh the screenshots only when the screens they show have
   changed, and keep `thumbnail.png` distinct from `screenshot-1.png`.
4. Keep `category` one of the store's fixed values, `Media`, `Productivity`,
   `Home`, `Networking`, `AI`, `Finance`, `Social`, `Developer` or `Others`.
   The build accepts a free-form value and the app then appears under no filter.
   PrintStash is `Productivity`, beside the other self-hosted file libraries.
   `port_map` must equal the published port (`3378`), and the store assigns a
   different one in review rather than us.
5. Validate with the store's own build, `./scripts/build_dist.sh`, which must
   finish without errors and write `dist/apps/org.printstash.printstash/` with a
   `meta.json` carrying both architectures, a `meta.es_ES.json`, and the assets
   under `assets/`. The store's CI also runs `docker compose config -q` on the
   changed app and a full build.
6. Open the PR saying whether it is a new app or an update, what changed, and how
   it was validated. A new app names the version it pins; an update names the
   release it follows.

Two blocks ship in that compose file on purpose. The service-level `x-casaos`
(environment, port and volume descriptions) is what older CasaOS clients read;
the v2 build keeps only the top-level block and drops the service one. Editing
both keeps the two generations of client described, and neither may contradict
the root `docker-compose.yml`.

### Cosmos Cloud: pull request to `azukaar/cosmos-servapps-official`

The Cosmos market is a repository, and an app is a folder under `servapps/<AppName>/`
holding `description.json`, `cosmos-compose.json`, `icon.png` and a `screenshots/`
directory (the store's build crashes without one). `catalogues/cosmos/PrintStash/` is
that folder, so a submission is a copy and, on each release, a version bump.

1. Fork and branch: `gh repo fork azukaar/cosmos-servapps-official --clone`, then
   `git switch -c printstash-X.Y.Z`.
2. Copy the package: `cp -r catalogues/cosmos/PrintStash <checkout>/servapps/PrintStash`.
3. **Every release:** bump `image` in `cosmos-compose.json` to the new tag. The
   manifest carries no version of its own; the image tag is what the store's CI
   reads.
4. **When the shots change:** refresh `screenshots/` from the repository's own
   `screenshots/` (the package ships `1.png`, `2.png` and `3.png`, copied from
   `01-vault-overview.png`, `03-gcode-viewer.png` and `02-model-detail.png`), and
   re-render `icon.png` from [`icon.svg`](../icon.svg) at 256×256.
5. Validate with the store's own validator before opening the PR:
   `npm install && node .github/scripts/validate-servapps.js PrintStash`. It renders
   the template with whiskers, the engine Cosmos installs with, checks the schema,
   fetches the image to confirm the announced architectures, and rejects a
   `minVersion` lower than the fields the template uses.
6. Open the PR saying whether it is a new app or an update and how it was validated.
   The store's CI runs the same validator on the diff, and a merge to `master`
   rebuilds the store that the Cosmos marketplace reads.

The manifest is deliberately plain JSON: it uses no whiskers conditionals, so the
file in this repository is the template the store renders, with placeholders only
ever replacing whole string values. `minVersion` is `0.8.0`, the version that covers
the install form, `post-install` messages and `{Passwords.N}`; nothing in the
template needs a newer Cosmos. The route carries no Cosmos auth because PrintStash
has its own accounts and the install dialog creates the first one, the same choice
Umbrel records as `PROXY_AUTH_ADD: "false"`.

The image stays pinned to a release tag and `cosmos-auto-update` is `false` on
purpose. Cosmos can only check for updates on a moving tag such as `latest`, and a
PrintStash release can require an upgrade step (see [`UPGRADE.md`](../UPGRADE.md)),
so the store listing follows releases the same way the Umbrel and CasaOS listings
do: the image moves when the manifest is updated here and a store PR lands.

### Runtipi: the PrintStash app store

Runtipi's official store no longer accepts new applications, so PrintStash
publishes its own Runtipi app store. Users add the store's repository URL once in
Runtipi's app-store settings and then install and update PrintStash like any other
app.

- **Repository:** `xiao-villamor/printstash-runtipi` (to be created), with
  `apps/printstash/` at the root: `config.json`, `docker-compose.yml`,
  `metadata/description.md` and `metadata/logo.jpg` exported from
  [`icon.svg`](../icon.svg). Structure it following
  <https://runtipi.io/docs/guides/create-your-own-app-store>.
- **Every release:** copy `catalogues/runtipi/printstash/` into `apps/printstash/`,
  increment `tipi_version` (Runtipi offers the update when it changes), and set
  `updated_at` to the release time in milliseconds. Commit and push to the store's
  default branch.
- **Once:** list the store in Runtipi's community app stores discussion
  (<https://github.com/runtipi/runtipi/discussions/categories/app-stores>) and
  link it from the PrintStash documentation.

### Before a first submission

Install each manifest on a real host from your own fork or store first: Runtipi
custom app stores, Umbrel community app stores, CasaOS custom store URLs and
Cosmos third-party stores all accept one. Confirm the whole first run: the owner
signs in, is sent to the storage step, chooses storage and uploads a model. Behind
each store's proxy is where `Host` handling is decided, and CI cannot see it.

The ports in `config.json` (Runtipi), `umbrel-app.yml` (Umbrel) and the published
port in the CasaOS compose file are host ports that must be unique within each
store; a store reviewer may assign different ones. Cosmos publishes no host port:
its route proxies to container port `3000`.
