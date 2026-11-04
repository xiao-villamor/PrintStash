# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Home self-hosters: individual makers with one to a few 3D printers, often
Klipper/Moonraker, who run PrintStash in Docker on a home server, NAS or
homelab box. They are technical enough to run containers and mount volumes.
Their job is to keep every Model's source files, the G-code that actually
printed well, slicer settings and print results in one place they own, so a
reprint months later starts from a known-good file instead of a re-slice and a
guess.

Small print farms and multi-user shops are not the design target. Multi-user
features (roles, API keys, OIDC) exist but serve the self-hoster's household or
homelab, not a team workflow.

## Product Purpose

PrintStash is a self-hosted library for 3D Models, G-code Revisions and print
history. Success means a user can find any Model, see which Revision is
recommended and why (its outcome, notes and measured prints), and send it to a
printer without leaving the app or trusting a cloud service.

## Positioning

The differentiator is the tested G-code Revision tied to real hardware: Models
are deduplicated logical assets (by source-mesh sha256) with versioned
Artifacts, and each G-code Revision carries outcome bookkeeping, a single
recommended marker, and measured print history pulled from the user's own
printers. Model-sharing sites stop at the mesh and slicer project folders stop
at the file; PrintStash keeps what printed, on which printer, and how it went.
All of this is local-first: SQLite and local storage by default, with no
required cloud, queue or Redis dependency.

## Operating Context

- Upload files and archives, index mounted folders or NAS shares in place, or
  review browser captures from model sites, then organize into collections,
  tags, saved views and Multipart Models.
- Compare Revisions and slicer settings, mark outcomes, open files in
  OrcaSlicer, Bambu Studio or PrusaSlicer, and receive G-code from the
  OrcaSlicer post-processing hook.
- Monitor printers, send G-code, control prints and queue copies; track
  filament, cost and time, optionally with Spoolman.
- Operate the install: backups, Vault audits, trash recovery, storage sources.
- Domain terms in `CONTEXT.md` (Model, Artifact, Revision, Multipart Model,
  live/trashed, storage key) are binding in UI copy as well as code.

## Capabilities and Constraints

- PrintStash does not slice models and does not verify that G-code is safe for
  a printer. UI must never imply either.
- Provider support levels are documented in `docs/provider-support.md` and
  `docs/known-limitations.md`: Moonraker/Klipper is primary; Bambu LAN,
  PrusaLink, OctoPrint and Elegoo providers are beta. Each provider declares
  its capabilities and the UI disables what a printer cannot do. Roadmap items
  (`docs/roadmap.md`) are not shipped features.
- Every UI string is localized: English and Spanish catalogs in
  `frontend/src/locales/` (`en.json`, `es.json`) must both be maintained.
- The web app is Vite + React + TypeScript; a responsive mobile layout and a
  browser extension already exist.
- Undecided: whether mobile parity and a formal accessibility standard are
  binding product requirements (see Accessibility & Inclusion).

## Brand Commitments

- Name and logo are governed by `TRADEMARKS.md`; logo files are
  `frontend/public/logo.svg`, `frontend/public/logo-dark.svg` and `icon.svg`.
- Personality is a productivity tool, not a showy consumer product (recorded
  with the visual system in `DESIGN.md`).
- License: AGPL-3.0. Public docs live at printstash.org.

## Evidence on Hand

- Real product screenshots and GIFs in `screenshots/` (library grid, model
  detail, G-code viewer, revision compare, printers, statistics, settings).
- `CHANGELOG.md`, `UPGRADE.md` and `docs/` for shipped behaviour and limits.
- No testimonials, user counts, customer names or benchmark claims are
  recorded for public use; do not invent them. Performance measurements live
  in private `reports/` and must not be quoted publicly.

## Product Principles

1. The known-good file wins: surface the recommended Revision, its outcome and
   its measured history before anything else about a Model.
2. Honest capability: show what a printer, provider or file type actually
   supports; disable the rest and label beta as beta.
3. Your files, your hardware: never require a cloud service, and never put the
   user's originals at risk (library sources stay read-only or create-only).
4. One library, many views: a Model is one addressable thing wherever it
   appears, including inside Multipart Models; presentation never duplicates or
   moves ownership.

## Accessibility & Inclusion

Bilingual UI (English and Spanish) is required. No formal accessibility
standard has been adopted as a product requirement yet; this is an open
decision.
