# Real end-to-end tests

These Playwright specs drive the **real** FastAPI backend against a throwaway
SQLite DB + temp data dirs — every create/edit/delete actually hits the database.
This is the difference from `tests/e2e/` (the fast mock-API smoke suite).

```bash
pnpm test:e2e:real
```

`playwright.real.config.ts` boots the application plus its printer emulator:

- `scripts/start-backend.sh` — wipes state, runs Alembic, launches uvicorn on
  `:8410` against a temp DB under `.data/` (gitignored).
- Vite on `:3310` with `VITE_API_URL` pointed at that backend.
- The standalone Moonraker/Spoolman emulator used by fleet scenarios.

Storage-provider setup is deliberately isolated because it must begin with an
unconfigured instance and restart after changing transports:

```bash
pnpm test:e2e:storage
```

`playwright.storage.config.ts` owns a separate backend, Vite server, and
loopback WebDAV server. The storage spec always runs the WsgiDAV contract. To
enable the additional real-Nextcloud contract, provide a reachable URL (the
storage launcher starts the official Apache image when Docker is available):

```bash
PLAYWRIGHT_STORAGE_NEXTCLOUD_URL=http://127.0.0.1:8780 pnpm test:e2e:storage
```

The launcher uses the same digest-pinned Nextcloud image and administrator
credentials as the backend provider contract. The contract configures Nextcloud
through Settings, restarts the API, verifies a non-empty remote G-code object,
checks the confirmation guard, and asserts guarded purge returns `blocked` while
the exact remote object remains. Without Docker or the URL it is reported as a
deterministic Playwright skip rather than a false compatibility pass. CI runs
both real-backend suites.

The optional SFTP lifecycle uses the OpenSSH contract harness when
`PLAYWRIGHT_STORAGE_SFTP_HOST` and `PLAYWRIGHT_STORAGE_SFTP_HOST_KEY` are set;
the port, username, and password can be overridden with the corresponding
`PLAYWRIGHT_STORAGE_SFTP_*` variables. It verifies guarded confirmation and a
blocked retained cleanup outcome.

`helpers.ts` seeds the first admin via `/setup` once and injects a real JWT into
the browser, so tests boot authenticated. The suite runs serially on one DB, so
tests use unique (timestamped) names and clean up after themselves.

Requires the backend dev venv (`backend/.venv`); falls back to `uv run`.

## Coverage

auth (UI login, wrong-password, username + API-key login then revoke) ·
vault (search, tag filter, list/grid toggle, empty state, narrow responsive toolbar) · collections
(create / nest / delete / recursive-delete non-empty from the sidebar) ·
documents (markdown editor, collection README, GFM tables) · tags (quick create/assign from a card,
global delete) ·
uploads (mesh-only source, BGCODE metadata, into a collection) · filament & printer presets
(create / edit / delete) · model lifecycle (upload → edit → trash → restore →
purge) · model detail (edit tags with save/cancel, log a manual print, download
a revision) · G-code revisions (add, auto-recommend, re-recommend,
status, compare) · public share links (view-only vs downloadable, revoke → 404) ·
multipart sets (empty-set first action, external cover, tags, favorites, reusable members) ·
RBAC (create user, grant collection access, non-admin sees only granted
collections, view vs edit role gates editing + deleting) · user management
(promote/disable/reset password) · API keys · settings overview (system status
and vault stats) · supervised API restart · display currency · auto-mark-known-good toggle · metadata
export (JSON/CSV) · manual backup · notification channels (add webhook + delete) ·
About (running version + changelog) · design customization (metadata visibility,
card-metric slots + reset) · printer add/remove · cross-cutting (theme
persistence, health version, routes free of uncaught errors).

`util.ts` uploads a model through the real ingest flow; its G-code embeds the
model name so the backend's content-hash dedupe doesn't collapse separate
uploads. `helpers.ts` also exposes `authBundleFor`/`authedContext` to drive a
second browser as a non-admin user.
