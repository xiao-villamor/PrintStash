---
title: User guide
description: The full loop — get files in, inspect them, find them again later.
---

This is the core PrintStash loop end to end: get files in, understand what you've
got, and find it again six weeks from now. It describes what the app actually
does today, with no promises about future work. If you're fuzzy on terms like
*model*, *artifact*, or *recommended revision*, skim
[Core concepts](/PrintStash/concepts/core-concepts/) first — it'll make this
shorter.

The questions this loop answers:

- What did I slice, and when?
- Which G-code revision was the one that came out right?
- What slicer, material, and printer settings produced it?
- What does the toolpath actually look like?
- Where is it — and is the printer ready for it?
- Can I get my metadata back out?

## 1. Get a model in

Open the grid (`/`). On a fresh install it's empty; that's the moment to upload
something.

Hit the upload modal and drop in an STL or 3MF. You can assign a collection right
there — say `Functional/Brackets` — and add tags inline if the tag doesn't exist
yet. The upload runs in the background and shows up in the task center, so you can
keep working while the thumbnail renders. When it's done, a model card appears in
the grid.

You don't have to upload mesh and G-code together. The modal accepts mesh-only,
G-code-only, or both at once — whatever you have on hand.

## 2. Add G-code and watch metadata appear

Upload a G-code file for that same model. Open the model detail page and look at
the **Overview** tab: PrintStash has read the slicer comments and filled in the
slicer name, layer height, infill, material, estimated print time, filament
weight/length, and printer model where the slicer recorded it. This works across
OrcaSlicer, PrusaSlicer, Bambu Studio, Cura, and Klipper/Orca-style profiles —
though exactly which fields appear depends on what your slicer wrote, so blanks
are normal.

Now look at the two viewers:

- **3D model** — the mesh, with solid / x-ray / wireframe modes, fit-to-view,
  a build-plate grid, zoom, and a screenshot button. 3MF and OBJ are converted to
  a cached STL for preview; STL streams directly.
- **G-code toolpath** — the sliced path for the latest/recommended G-code. Scrub
  the layer slider, toggle travel moves, and turn on the bed overlay (drawn from
  known printer profiles).

The toolpath viewer is a visualization aid, not a slicer-grade simulator — it
won't validate macros, acceleration, or pressure advance. It's there so you can
*recognize* a file, not certify it.

## 3. Build the revision story

This is where the app earns its keep over a folder of files.

- Upload a second G-code revision, or edit the fields on an existing one in the
  **Revisions** tab.
- Set each revision's status — `needs_test`, `known_good`, or `failed`.
- Drop a short note that future-you will understand: `PETG, +5°C`,
  `tighter fit`, `warped at corners`.
- Mark the best one **recommended**. Remember the invariant: marking one
  recommended clears the marker from the rest, so there's always exactly one
  good answer.
- Use **compare** to put two revisions' slicer/material/print settings side by
  side when you're trying to remember what you changed.

Then log how it actually printed. The **History** tab takes manual print-job
entries, or — if you've connected a Moonraker printer — imports matching history
straight from the printer (see step 5).

## 4. Find it again

Weeks later, back at the grid:

- Search by model name.
- Filter by collection (breadcrumbs let you drill into the tree) or by tag.
- Filter by printer presence to see what's already loaded where.

The model keeps its mesh, every revision, the notes, and the history together —
so "find it again" is one search, not an archaeology dig through filenames.

## 5. Connect a printer

Add a Moonraker/Klipper printer from **Printers** and open its detail page. From
there you can watch live status, send a vault G-code file to it, sync its remote
file inventory, and import its print history onto the matching models.

Open the **Diagnostics** tab to see the provider's support level, capability
checks, and which actions aren't supported. PrintStash is Moonraker-first; Bambu
LAN is beta and limited to status plus pause/resume/cancel. The full breakdown is
in [Printers & providers](/PrintStash/guides/printers/).

## 6. Get your data back out

Your library shouldn't be a trap. From **Settings → Overview** you can export
metadata as JSON or CSV (JSON for full library context, CSV for one row per
stored file). Exports include model fields, collections, tags, revision data, and
slicer/mesh metadata.

They deliberately **exclude** the raw STL/G-code blobs, secrets, and printer
credentials — and they require auth, because filenames, materials, and print
history can leak more about a job than you'd expect. For moving or recovering an
entire install (blobs and all), use full
[backup & restore](/PrintStash/guides/backup-and-restore/) instead.

## Skip the manual upload: the OrcaSlicer hook

Once the loop above feels natural, stop uploading G-code by hand. PrintStash
ships a post-processing script (`scripts/printstash_orca_push.py`) that you add to
OrcaSlicer's post-processing settings. After every slice, OrcaSlicer runs it and
the exported G-code lands in the vault as a new revision automatically.

A couple of things that make it pleasant to live with:

- It uses only the Python standard library — nothing to `pip install`.
- It authenticates with your username and a PrintStash **API key** (create one
  under **Settings → Access**), not your password.
- If PrintStash is offline, it exits `0` — so a down vault never breaks your
  slicing or export.

Point it at your upload endpoint with the API key and forget about it; new
revisions just show up.
