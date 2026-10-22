# Release procedure

Canonical validation detail: `docs/release-validation.md` (clean install,
upgrade-from-volume, smoke checks) and `docs/manual-testing.md` (full browser
sweep). This file is the ordered checklist that ties it together.

## Readiness gate

- [ ] Confirm the release scope. Every planned bug or feature is merged through
      its own PR to `main`, together with the migrations, documentation, and
      validation that change requires.
- [ ] `main` is up to date and its required `PR gate` is green. Release from
      this integrated state; do not collect work on a version-number branch.
- [ ] Choose X.Y.Z from the merged contents. While 0.x, patches contain fixes
      only; a release containing features increments the minor version.
- [ ] If protected `main` requires a PR for release metadata, create
      `chore/release-X.Y.Z` from the current `main`. This branch may contain only
      the version, changelog, and release-documentation edits below.

## Prepare the release commit

- [ ] Bump the triple (`backend/pyproject.toml`, `backend/app/core/config.py`
      `app_version`, `frontend/package.json`) **and** add a matching
      `CHANGELOG[0]` entry to `frontend/src/lib/changelog.ts` (its own test,
      `changelog.test.ts`, checks this against `package.json` and fails CI on
      its own if skipped) — one commit: `chore(release): bump to X.Y.Z`.
- [ ] Before publication, use the new version tag without a digest in the Umbrel
      and TrueNAS source packages. Their new image digest does not exist yet.
      Never reuse the previous version's digest with the new tag. After publication,
      pin the verified index digest in both sources before copying to store forks.
      Run `PRINTSTASH_STORE_SUBMISSION=1 uv run pytest
      tests/repo/test_catalogue_manifests.py -q` from `backend/`; this strict gate
      rejects unpinned packages even though preparation checks accept bare tags.
- [ ] Bump the app-store manifests under `catalogues/` to the new version in
      the same commit (`tests/repo/test_catalogue_manifests.py` fails until
      they match). The stores themselves are updated after publishing (below).
      The TrueNAS package's source is here too, in `catalogues/truenas/printstash/`,
      so it is bumped in the same commit like the rest and copied to `truenas/apps`
      after the tag (TrueNAS section below).
- [ ] Promote the accumulated `CHANGELOG.md` `## Unreleased` contents to
      `## X.Y.Z`, restore an empty `## Unreleased`, and verify the entry matches
      the condensed in-app changelog (format in
      [conventions.md](conventions.md)).
- [ ] Run focused local checks for the release-only metadata changes. The
      merged commit's `CI` result covers the regular PR gate; `Deep CI` below
      covers full suites and coverage without repeating them locally.
- [ ] Browser extension, when changed: run affected local tests and browser
      flows per [capture.md](capture.md); `CI` builds and tests the package,
      and `Deep CI` runs its real-backend and ChromeDriver checks.
- [ ] Upgrade check: previous-release DB → `uv run alembic upgrade head` →
      app boots (self-hosters upgrade from old releases; CI has a
      migration-upgrade job, but run it locally for schema-heavy releases).
- [ ] Compose smoke: `docker compose up` →
      `/api/v1/health` returns the new version.
- [ ] If the release touches a provider: add a Hardware Validation Log row in
      `docs/provider-support.md` from a real smoke test, or carry the
      "needs real-world hardware validation" note in
      `docs/known-limitations.md`. Never leave it silently implied as done.
- [ ] Docs sweep: `docs/provider-support.md`, `docs/known-limitations.md`,
      `docs/roadmap.md`, docs pages the release invalidates — docs now live
      in the `printstash-landing` repo's `src/content/docs/docs/` (built as
      `/docs` on the same site, not a separate wiki repo) — (plus the
      gitignored `docs/feature-inventory.md` if you have it locally).

## Publish

- [ ] If a release-metadata PR was required, merge it to `main`; then update the
      local `main` and verify its HEAD contains the version triple and changelog.
- [ ] Run `Deep CI` manually on that exact `main` commit after all metadata
      changes have merged. Wait for a green result. The release workflow checks
      both `CI` and `Deep CI` for the tag's exact SHA and will stop on an absent,
      failed or running result; it does not re-run the suites.
- [ ] Tag that exact `main` commit:
      `git tag vX.Y.Z && git push origin vX.Y.Z`. CI publishes the GHCR image
      and the tag guard checks the version triple.
- [ ] `gh release create vX.Y.Z` with the format below.
- [ ] Update the app stores, following each store's section of
      `catalogues/README.md` (it is the source of truth for paths, pinning and
      per-store checks):
      - Wait for the image: `docker buildx imagetools inspect
        ghcr.io/xiao-villamor/printstash:X.Y.Z` lists `linux/amd64` and
        `linux/arm64`; note the digest for Umbrel.
      - **Confirm with the maintainer before opening any store PR or pushing to
        the Runtipi store.** They are public actions in other projects'
        repositories.
      - Umbrel: the ordered steps below, then a PR to
        `getumbrel/umbrel-apps` through that repository's own
        `umbrel-update-app` and `umbrel-test-app` skills.
      - CasaOS / ZimaOS: a PR to `IceWhaleTech/CasaOS-AppStore`, validated with
        its `./scripts/build_dist.sh`.
      - Cosmos Cloud: a PR to `azukaar/cosmos-servapps-official`, after
        `node .github/scripts/validate-servapps.js PrintStash` passes in the fork;
        the package is `catalogues/cosmos/PrintStash/`.
      - Runtipi: push the updated app to the PrintStash Runtipi app store
        (`xiao-villamor/printstash-runtipi`) with `tipi_version` incremented.
      - TrueNAS: copy `catalogues/truenas/printstash/` into a branch of your fork
        of `truenas/apps` and open the PR; the ordered steps are in the TrueNAS
        section below.
      - Unraid: nothing to do; Community Applications re-reads the template.
      - Link each store PR from the GitHub release notes, and record anything a
        store reviewer changed (for example a reassigned port) back in
        `catalogues/`.
- [ ] Announce in the public roadmap discussion. Changelog says what's
      protected; never quotes private `reports/` analysis.
- [ ] Update the "Where we are" block in `SKILL.md` (this skill).

## Umbrel App Store, every release

`catalogues/umbrel/printstash/` and `printstash/` in `getumbrel/umbrel-apps` are
the same package: the catalogue is the source, the store copy is what umbrelOS
installs. Both move in one release, or the listing keeps serving the old image.
`catalogues/README.md` holds the field-by-field contract; this is the order.

1. Read the digest of the published image:
   `docker buildx imagetools inspect ghcr.io/xiao-villamor/printstash:X.Y.Z`
   must list `linux/amd64` and `linux/arm64`. Copy the digest it reports for
   that tag; it is the multi-arch index digest, not a per-architecture one.
2. The release commit bumps `version` and the image tag. After publication,
   replace that bare tag with
   `image: ghcr.io/xiao-villamor/printstash:X.Y.Z@sha256:<digest>` in the source
   package and commit it before store submission. Source tests require the app
   version and validate digest syntax when present; store validation requires the
   published digest and verifies it against the registry.
3. Replace `releaseNotes` with this release's notes from the CHANGELOG entry:
   user-visible features, fixes, and any upgrade or backup action, plus the
   upstream release link. Keep two blank lines between paragraphs and one
   before and between list items; YAML folds single newlines into spaces.
   Never leave the previous version's notes in place, and never write the
   notes only in the store copy.
4. Copy `catalogues/umbrel/printstash/` over `printstash/` in your fork and
   follow `umbrel-update-app`. `submission` keeps pointing at the first PR;
   the store does not track later ones. Leave `gallery`, `icon` and
   `storage: dataRoot: data` as they are.
5. `npm run lint:apps -- printstash --check-images` must pass, and its digest
   check must resolve to the digest in the compose file, then `git diff
   --check`.
6. Test through `umbrel-test-app`: install on umbrelOS, sign in with the
   credentials Umbrel shows, choose storage, upload a model, restart, and
   confirm the library survives. A release that cannot be installed that way
   is not ready.
7. Open the PR, link it from the GitHub release notes, and record anything
   the maintainers change (a reassigned port, a pinned image, edited copy)
   back into `catalogues/umbrel/printstash/` in the same release. Otherwise
   the next release reintroduces it.

## TrueNAS community catalog, every release

The TrueNAS package is submitted to `truenas/apps`, and its source sits here with
the other manifests, in `catalogues/truenas/printstash/`: the same `app.yaml`,
`ix_values.yaml`, `questions.yaml`, `item.yaml`, README, template and test values
that the catalog reads. The release commit bumps it like every other listing, and
`tests/repo/test_catalogue_manifests.py` holds the fields that matter, so a drifted
copy fails in this repository rather than on a user's server. After the tag, the
package is copied into a fork of `truenas/apps` and opened as a PR.

1. Wait for the image, as for the other stores:
   `docker buildx imagetools inspect ghcr.io/xiao-villamor/printstash:X.Y.Z`
   must list `linux/amd64` and `linux/arm64`. TrueNAS hosts are `linux/amd64`.
2. The release commit sets `images.image.tag` in
   `catalogues/truenas/printstash/ix_values.yaml` to `"X.Y.Z"`, updates
   `app_version` in that directory's `app.yaml`, and bumps `version` there
   (the catalog's own version, patch level per release). After publication,
   append `@sha256:<index digest>` to that image tag in the source and commit
   it before store submission. The submitted catalog pins tag and digest together.
3. After the tag, in a branch of your fork of `truenas/apps`: copy the files
   of `catalogues/truenas/printstash/` over `ix-dev/community/printstash/`, leaving
   the vendored `templates/library/` alone, then run `devbox run copy-lib` there.
   It vendors the library named by `lib_version` and rewrites `lib_version_hash`;
   the generated library is committed with the app.
4. Regenerate the derived metadata in that checkout:
   `python3 ./.github/scripts/generate_metadata.py --train community --app
   printstash`. It needs Docker, because it renders through the validation image,
   and it rewrites `app.yaml` and `item.yaml`. Commit what it writes: the catalog's
   `check_metadata` job fails when the committed files differ from its output.
5. Run the app test before opening the PR:
   `./.github/scripts/ci.py --app printstash --train community --test-file
   basic-values.yaml --wait=true`. The catalog's `app-test-suite` workflow runs the
   same deploy on the PR, but a release that cannot start is worth finding first.
6. Open the PR from the fork with the repository's `app_addition.md` template for
   the first submission and its default template afterwards, stating that it is an
   update, the app version, and what was tested. Link it from the GitHub release
   notes like the other stores.
7. Copy anything the maintainers change (a reassigned default port, edited install
   notes or question descriptions) back into `catalogues/truenas/printstash/` in the
   same release, so the next one does not reintroduce it.

Two things the package pins, so a release that changes either needs the catalogue
changed with it: the container port `3000` (the template maps the published port
onto it, because the image's nginx config fixes it) and the `PUID`/`PGID` runtime
identity (the template sets the container user, and the catalog library passes both
variables from that answer). A release that moves the data root out of `/data`, or
that stops reading `PUID`/`PGID`, breaks the listing until the template follows.
Everything else, including a new environment variable, needs no TrueNAS change: the
install form carries an Additional Environment Variables group.

## App-store packages ship the tagged release

A store pull request pins one tag with its digest, so a package may only set what
that release reads. `catalogues/` tracks `main`, and
`tests/repo/test_catalogue_manifests.py` validates it against the working tree's
`Settings`, so a package can run ahead of the release it is submitted for.

Before opening any store pull request, read the settings at the tag:

```sh
git show vX.Y.Z:backend/app/core/config.py
```

Every variable the package sets must appear there. A variable added after the tag
is inert in the image the store installs, because `Settings` is `extra="ignore"`:
decide once per release whether to ship it anyway and say so in the pull request
body, or hold it until the release that reads it. v0.14.0 is the worked example.
`VAULT_MESH_MEMORY_BUDGET_FRACTION` is in it; `VAULT_COMPUTE_MODE`,
`VAULT_COMPUTE_MEMORY_MB` and `VAULT_EMBEDDING_MEMORY_BUDGET_FRACTION` landed
after that tag.

## App Store first submission, once per store

`catalogues/README.md` is the per-store contract. This is the order the first
submission adds on top of a release. Umbrel is written out because it is already
packaged; the other stores follow the same shape with their own paths and checks.

1. Fork the store repository and branch `printstash-X.Y.Z` from its default
   branch.
2. Copy `catalogues/umbrel/printstash/` to `printstash/` at the store root,
   `data/.gitkeep` included. The package is not `apps/printstash/`.
3. Leave `gallery: []`, omit `icon`, keep `submitter`, and leave
   `submission: ""` for the moment.
4. Run `npm run lint:apps -- printstash --check-images`. `submission` is the one
   error that cannot be fixed before the pull request exists. Every other error
   is real, and `restart: unless-stopped` is the one expected warning: a restart
   requested from Settings is a graceful exit that can return status 0, which
   `on-failure` would not relaunch.
5. Open the pull request, then set `submission` to its URL in a second commit on
   the same branch. The linter rejects an empty `submission`, so the first push
   cannot be clean; after that second commit it is.
6. Put in the body: app version, upstream project URL, image source, testing
   performed, default credentials, the restart decision, the `app_proxy`
   decision and what stays behind Umbrel's login, plus screenshots and a logo
   reference. Never commit gallery or icon assets; the Umbrel team creates the
   official ones before merge.
7. Test through `umbrel-test-app` before asking for review: install on umbrelOS,
   sign in with the credentials Umbrel shows, choose storage, upload a model,
   restart, and confirm the library survives.
8. Once merged, the listing lives at `https://apps.umbrel.com/app/printstash`.
   That page is the citation target for the change, so record the URL in the
   release notes and in the work that prompted it.

## GitHub release format

Match existing releases (`v0.8.3`, `v0.8.1`):

- **Title:** `vX.Y.Z — short theme` (a few words; omit the theme only if the
  release has no unifying one).
- **Body:** the `CHANGELOG.md` entry for that version, `###` headers
  (Security/Fixed/Performance/Added as applicable). Leading bold callout line
  only for upgrade-behavior warnings. No `##`/`###` version header at the top
  — the title already carries the version.
- **End with:**
  `**Full changelog:** https://github.com/xiao-villamor/PrintStash/compare/vPREV...vX.Y.Z`

Example title: `v0.8.5 — CI/ops hardening`.
