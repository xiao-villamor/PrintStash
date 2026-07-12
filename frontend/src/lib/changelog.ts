// ─────────────────────────────────────────────────────────────────────────
// SINGLE SOURCE OF TRUTH for the app changelog + repo identity.
//
// Keep this updated on every release / notable user-facing change, then bump
// the version in: backend/pyproject.toml, backend/app/core/config.py
// (app_version), and frontend/package.json. The Settings → About tab renders
// CHANGELOG[0] as the current version's details.
//
// Newest release goes FIRST. CHANGELOG[0].version MUST equal the version in
// frontend/package.json, which MUST equal backend/pyproject.toml's version —
// changelog.test.ts enforces both, so a forgotten bump fails CI instead of
// silently leaving the About tab on an old release.
// ─────────────────────────────────────────────────────────────────────────

export const GITHUB_REPO = "xiao-villamor/PrintStash";

export interface ChangelogEntry {
  version: string;
  date: string; // human-readable, e.g. "Jun 2026"
  changes: string[];
}

export const CHANGELOG: ChangelogEntry[] = [
  {
    version: "0.9.0",
    date: "Jul 2026",
    changes: [
      "PrusaLink beta: connect local Prusa FDM printers using Digest credentials or a legacy API key, with status, files, send/start, and print controls",
      "Elegoo Neptune 4, Pro, Plus, and Max now have a guided setup preset backed by the stable Moonraker integration",
      "Elegoo Centauri Carbon and Carbon 2 beta support adds local status and print controls through each model's native LAN protocol",
      "Printer setup now presents Moonraker, Elegoo Neptune 4, PrusaLink, and Bambu LAN fields without exposing stored secrets",
      "Bambu LAN beta: upload plain-text Vault G-code over local FTPS, then optionally start it through local MQTT with an explicit Send & Print choice",
      "Bambu sends verify the printer is idle before creating a transfer job; upload-only remains the safe default",
      "Printer actions and diagnostics now come from each provider's advertised capabilities, so unsupported controls stay clearly disabled",
      "Printer management refreshed: optional model artwork, cleaner provider badges, a clearer idle/offline Status tab, compact temperature telemetry, and a new Settings tab for editing connection and display details",
      "Dashboard-wide polish standardizes page widths, headers, navigation, settings sections, empty states, errors, confirmations, and browser tab titles",
      "Motion now uses one fast, reduced-motion-aware system across model grids, batch selection, tabs, modals, drawers, menus, and toasts",
      "Rapidly submitting Add printer no longer creates duplicate database rows or unexpectedly advances the printer URL ID",
      "Connection failures use bounded backoff, while Bambu polling failures now correctly mark the printer offline and recover on the next successful status",
      "Live job progress writes are coalesced and repeated database failures are circuit-broken to avoid write storms",
    ],
  },
  {
    version: "0.8.5",
    date: "Jul 2026",
    changes: [
      "Login and refresh are now rate-limited to slow down credential-stuffing attempts",
      "Backup and restore actions are now recorded in the audit log",
      "Fixed audit-log entries leaking printer/storage/notification secrets in their change diffs",
      "Fixed S3/R2-compatible storage (including MinIO) failing to auto-create its bucket on first boot",
      "Fixed a rejected send-to-printer leaving the print job stuck \"uploading\" forever",
      "Fixed the send-to-printer picker keeping a deleted revision selected, and failed sends now show which printer failed and why",
      "Faster model versioning, permission checks, and profile usage counts; less database load from live print status syncing",
      "CI now scans dependencies for known vulnerabilities and verifies database upgrades on every change",
    ],
  },
  {
    version: "0.8.4",
    date: "Jul 2026",
    changes: [
      "The JWT signing secret can no longer boot with the shipped default value; existing sessions are invalidated once on the upgrade that generates a real one",
      "SQLite foreign key constraints are now enforced, after repairing any orphaned rows left by earlier versions",
      "Outbound fetches are now pinned to the address the SSRF guard validated, closing a DNS-rebind gap",
      "Fixed printer history re-import duplicating past jobs when a printer changed filename casing between polls",
      "Backup restore no longer races in-flight garbage collection, library scans, or live print syncing",
      "Faster collection permission checks, atomic print-job ingestion, non-blocking printer status updates, and a cached dashboard storage figure",
    ],
  },
  {
    version: "0.8.3",
    date: "Jun 2026",
    changes: [
      "Fixed the hourly cleanup job deleting uploaded document files (local storage only; S3/R2 was unaffected)",
      "Fixed backups omitting document files, so restoring a backup no longer silently loses them",
      "Fixed S3/R2 existence checks hiding credential or permission errors as \"file does not exist\"",
    ],
  },
  {
    version: "0.8.2",
    date: "Jul 2026",
    changes: [
      "Drag a model card onto a folder in the grid view to move it there",
      "Added a trademark policy (TRADEMARKS.md) protecting the PrintStash name and branding; the project stays licensed under AGPLv3",
      "Fixed remember-me login not persisting the session correctly, and shortened the token lifetime for accounts that don't opt in",
      "Fixed a duplicate collections invalidation that could cause extra refetches",
      "Repaired docker-compose.light.yml",
    ],
  },
  {
    version: "0.8.1",
    date: "Jun 2026",
    changes: [
      "Printer controls: set the hotend and bed target temperature right from a Moonraker printer's Status tab, with one-tap PLA/PETG/ABS preheat presets and a Cooldown button",
      "Home the printer (all axes) and an Emergency stop button — both confirm before acting, and all of these controls are hidden for printers that don't support G-code commands",
      "Mobile: pages now use the dynamic viewport height so they scroll fully under the browser chrome instead of cutting off at the bottom",
      "Mobile: the model detail page scrolls when its settings/files panel is taller than the screen, and content no longer hides behind the bottom navigation bar on the vault, model, and document pages",
      "Thumbnails render with brighter, higher-contrast mesh lighting on the dark theme",
    ],
  },
  {
    version: "0.8.0",
    date: "Jun 2026",
    changes: [
      "Spoolman integration: connect a self-hosted Spoolman instance under Settings → Spoolman to track filament inventory and per-print consumption — off by default, with an optional API key and a Test connection button",
      "Pick which spool a print uses when sending a job to a printer or logging a print manually; the spool is shown on the print record",
      "Filament presets sync from Spoolman: a 'Sync from Spoolman' button on the Profiles page imports your Spoolman filaments as read-only presets (cost, material, density, diameter) so you maintain filament data in one place — local-only presets stay editable",
      "Prints that used a synced spool get exact cost and more accurate weight, using the spool's real price and density/diameter instead of estimates",
      "When a Moonraker-measured print completes, PrintStash decrements the selected Spoolman spool by the real grams used — no double-entry of your inventory",
      "Double-count safety: if Moonraker's own Spoolman integration is already tracking the active spool, PrintStash warns you and keeps its write-back off so a print is never counted twice",
      "Spoolman connection status is reported in the health endpoint and degrades gracefully — a Spoolman outage never blocks or fails a print",
      "Spoolman: Test connection now checks the address you typed (verify before saving), and Save/Test give clear success and error feedback",
      "Collection documents & READMEs: attach docs to any collection — write Markdown in a built-in editor (live preview, paste or drop images) or upload PDFs and files",
      "PDFs open inline in a themed viewer with page navigation and zoom, instead of the browser's default PDF chrome; new Markdown docs open ready to edit and aren't saved until you choose to",
      "The logo and a document's Back button now return you to the Documents tab when that's where you were",
    ],
  },
  {
    version: "0.7.3",
    date: "Jun 2026",
    changes: [
      "PrusaSlicer binary G-code (.bgcode) is now a supported file type: upload, import, and shared-volume scans read its slicer metadata and embedded thumbnail just like a text .gcode",
      "Binary G-code can't be printed by Moonraker/Klipper or Bambu and has no in-browser toolpath, so send-to-printer and the G-code preview are disabled for .bgcode files (metadata and thumbnail still show)",
    ],
  },
  {
    version: "0.7.2",
    date: "Jun 2026",
    changes: [
      "Database migrations now run automatically when the app starts — there's no separate migration step, and editing or removing the Compose command can no longer skip them",
      "Fresh installs and existing databases both come up cleanly on SQLite and PostgreSQL; a database that was once started without migrations is detected and adopted safely, without changing any data",
      "Deleting a model now returns you to the folder you were browsing instead of jumping back to All Models",
      "The PrintStash logo now takes you back to the collection you were in, rather than always to All Models",
    ],
  },
  {
    version: "0.7.1",
    date: "Jun 2026",
    changes: [
      "Upload many files at once, or a whole folder — the folder structure is kept as nested collections instead of being flattened",
      "Big libraries no longer run the app out of memory during a scan: files too large for your machine's RAM are skipped safely (still indexed, and 3MF keeps its embedded preview), memory is freed between files, and large models are processed in smaller pieces",
      "New settings to tune memory use on small or busy servers (max concurrent renders, memory budget, render chunk size) — see the configuration docs",
      "The “All Models” view now counts your whole library, not just models sitting at the top level",
    ],
  },
  {
    version: "0.7.0",
    date: "Jun 2026",
    changes: [
      "Notifications: get alerted when a print completes, fails, or is cancelled, or when a printer goes offline — delivered to webhooks, Discord, Telegram, or ntfy",
      "Set up channels under Settings → Notifications with per-event and per-printer toggles, a Test button, and a recent-deliveries log; failed sends retry automatically",
      "Smoother, better-framed model thumbnails — organic models render as smooth surfaces in a 3/4 hero view instead of a faceted, top-down blob",
      "Fixed the 3D viewer laying models on their side with the floor grid cutting through them — models now stand upright and sit on the grid",
      "“Open in slicer” now works on self-hosted instances, opening OrcaSlicer/Bambu Studio with the right file and format",
      "Zip imports keep the archive’s folder structure as nested collections instead of flattening everything into one",
      "Fixed wildly wrong filament length on OrcaSlicer G-code — a benchy could report millions of millimetres because a start-G-code comment was misread as a metres value; lengths (and the costs derived from them) are now correct",
      "Bed temperature now shows for OrcaSlicer and Bambu Studio G-code",
      "Infill percentage now shows for PrusaSlicer G-code",
      "Very dense meshes (multi-million-triangle lattice/gyroid models) skip thumbnail rendering to avoid out-of-memory crashes during library scans; the files are still indexed and 3MF keeps its embedded preview",
    ],
  },
  {
    version: "0.6.7",
    date: "Jun 2026",
    changes: [
      "Import whole collections and individual files by URL from Printables, MakerWorld, and Thingiverse",
      "Connect a MakerWorld account under Settings → Imports so model and collection downloads work — sign in with email + password (with the emailed verification code), or paste a session token for Google-linked accounts",
      "MakerWorld imports past the Cloudflare check using a headless browser",
      "Fixed collection imports reporting success when every model actually failed to download — they now fail with a clear reason (e.g. MakerWorld login required)",
    ],
  },
  {
    version: "0.6.6",
    date: "Jun 2026",
    changes: [
      "New Prometheus metrics endpoint at /metrics for Grafana/Prometheus dashboards — request latency, ingestion jobs, and live printer status; optionally protected with a bearer token (VAULT_METRICS_TOKEN)",
      "The health check now reports background-job and shared-volume scan status alongside database, storage, backup, and printer readiness",
      "Shared-volume scans interrupted by a restart no longer get stuck — they're reset on startup and picked up again on the next scheduled scan",
      "Unraid support: install PrintStash from Community Applications templates for the API and web UI, with a step-by-step setup guide",
    ],
  },
  {
    version: "0.6.5",
    date: "Jun 2026",
    changes: [
      "Fixed the app being unresponsive right after first-run setup — Upload, New collection, and the admin/settings menu now work immediately instead of needing a page reload",
      "Faster first load: pages are code-split so the initial visit only downloads the screen you're on",
      "Smaller production build — developer tooling no longer ships to end users",
    ],
  },
  {
    version: "0.6.4",
    date: "Jun 2026",
    changes: [
      "Settings -> Storage now lists available backups with one-click download to your computer",
      "Backups can be restored from the UI with a destructive confirmation step",
      "Admins can download backup archives through the API at /api/v1/backups/{id}/download",
    ],
  },
  {
    version: "0.6.3",
    date: "Jun 2026",
    changes: [
      "Bambu printers now report paused and finished prints correctly, and their print history follows the full job lifecycle",
      "Reprinting the same file records a new history entry instead of overwriting the previous print's outcome",
      "Library and trash lists page reliably — no more repeated or skipped models after a bulk import",
      "Model search is now case-insensitive on PostgreSQL, matching SQLite",
      "More accurate G-code details: fractional time estimates, Cura filament length, and genuine zero values (0% infill, an unheated bed) are no longer dropped",
      "Import hardening: look-alike domains are no longer mistaken for MakerWorld, and downloaded filenames are preserved in full",
    ],
  },
  {
    version: "0.6.2",
    date: "Jun 2026",
    changes: [
      "New Statistics dashboard: cost, filament, prints and print-time trends with top collections/filaments and a configurable currency",
      "Shared volumes (formerly “NAS folders”): mirror a folder on the server or a NAS",
      "Scheduled scans now use presets (hourly, daily, weekly…) or a custom cron expression, alongside manual “Scan now”",
      "Real-time watching keeps local folders in sync within seconds; network folders (NAS) automatically fall back to scheduled scans",
      "Fixed a 500 error when opening the Shared volumes settings tab",
    ],
  },
  {
    version: "0.6.1",
    date: "Jun 2026",
    changes: [
      "Import from URL now accepts Printables, MakerWorld, and Thingiverse model pages — paste the page you're on, no need to find the direct download link",
      "Fixed the About tab showing the previous release instead of the current one",
      "Fixed 3D model previews taking 20-30s to load — STL files now serve near-instantly",
    ],
  },
  {
    version: "0.6.0",
    date: "Jun 2026",
    changes: [
      "External libraries: mirror a NAS or local folder in place — files are indexed where they live, never copied",
      "Two-way sync: scans pick up added, removed, and edited files; web uploads and revisions write back into the folder",
      "Folder structure maps to collections (mirror mode), or route everything into one collection (single mode)",
      "Your files are never overwritten or deleted — trash and cleanup skip externally-linked files, and uploads never clobber existing ones",
      "Unmount-safe: a scan aborts instead of mass-deleting when the folder is missing or unexpectedly empty",
      "Fixed scheduled scans silently stopping after a library's first scan",
    ],
  },
  {
    version: "0.5.0",
    date: "Jun 2026",
    changes: [
      "Import models from a URL or a .zip archive, with selective per-file extraction",
      "STEP / STP CAD files: ingest, 3D preview, and thumbnails",
      "Public share links — expiring, read-only, view-only by default (optional download)",
      "Measured filament + print duration captured from the printer, with real per-print cost",
      "Auto-mark a revision known-good after its first successful print (toggle in Design settings)",
      "Delete G-code revisions from a model's Revisions tab",
      "Log print history against an ad-hoc printer name — no registered printer required",
      "Moonraker printer inventory now stays in sync, removes files deleted elsewhere, and supports deleting printer files",
      "Printer detail UI refreshed with Moonraker / Klipper config, diagnostics, and profile/settings-aligned styling",
    ],
  },
  {
    version: "0.4.0",
    date: "Jun 2026",
    changes: [
      "Frontend rebuilt on Vite + React Router (migrated off Next.js)",
      "TanStack Query caching: shared collections/tags cache, refetch on window focus, auto-refresh after edits",
      "Fixes the model detail page error and broken 3D preview / downloads under multi-user access",
      "Served by nginx with a same-origin API + WebSocket proxy",
    ],
  },
  {
    version: "0.3.0",
    date: "Jun 2026",
    changes: [
      "Multi-user access: collection-level roles (view / edit / admin) per user",
      "Admin user management and access controls",
      "Authenticated assets: thumbnails, 3D previews, and downloads now require sign-in",
      "Lossless WebP thumbnails",
      "Settings refinements and collections sidebar fixes",
    ],
  },
  {
    version: "0.2.0",
    date: "Jun 2026",
    changes: [
      "Profiles: inline auto-save editing, aligned columns",
      "Outliner: fixed subfolder expansion in the collections sidebar",
      "Settings: new About tab with version history",
      "Theme-aware browser favicon (light/dark)",
      "Catalog: tags are now removable",
      "UI/UX polish across model detail, grid, and filters",
    ],
  },
  {
    version: "0.1.0",
    date: "Initial release",
    changes: [
      "Self-hosted vault for STL/3MF/G-code assets",
      "Collections, tags, and drag-and-drop organization",
      "Moonraker/Klipper printer control (Bambu LAN beta)",
      "Filament & printer presets with cost tracking",
    ],
  },
];

/** Current app version = newest changelog entry. */
export const APP_VERSION = CHANGELOG[0].version;
