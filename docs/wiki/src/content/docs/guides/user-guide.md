---
title: User guide
description: The ingest, inspect, and find-again workflow end to end.
---

This guide walks the core PrintStash loop: get files in, inspect what's there,
and find it again later. It mirrors the demo walkthrough and shows current
behavior without promising future work.

PrintStash answers questions like:

- What did I slice?
- Which G-code revision was the good one?
- What slicer / material / printer settings were used?
- What does the sliced toolpath look like?
- Can I find it again later?
- Is a printer/provider ready?
- Can I export my metadata?

## 1. Library first impression

- Open the model grid.
- Browse collections, tags, and search; the grid shows your library state.
- Upload one STL/3MF and assign a collection such as `Functional/Brackets`.
- A thumbnail is generated and a model card appears.

## 2. Metadata extraction

- Upload a G-code file for the same model.
- Open the model detail page.
- Review extracted slicer metadata: slicer, layer height, infill, material,
  estimated time, filament usage, and printer model where available.
- Open the mesh viewer for an STL/3MF/OBJ.
- Switch to the G-code toolpath viewer, scrub layers, and show travel/bed
  overlays when available.

## 3. Revision story

- Add a second G-code revision, or edit revision fields.
- Mark a revision as `needs_test`, `known_good`, or `failed`.
- Add a short note such as `PETG baseline` or `Tighter fit`.
- Mark the best file as **recommended**.
- Log a print-history entry for the recommended revision, or import matching
  Moonraker history if a printer is connected.

## 4. Find it again

- Return to the grid.
- Search by model name.
- Filter by collection or tag.
- The model keeps its files and revisions together.

## 5. Provider diagnostics

- Add a Moonraker printer and open its detail page.
- Open the **Diagnostics** tab to see support level, capability checks, and
  unsupported actions.
- PrintStash is Moonraker-first; Bambu LAN is currently beta
  (status and control only). See [Printers & providers](/PrintStash/guides/printers/).

## 6. Data portability

- Open **Settings → Data export**.
- Download JSON and CSV.
- Review vault stats, storage usage, and model-card metric choices.
- Exports are metadata-only and require auth, because filenames, materials, and
  print history can still be sensitive.

## Pushing G-code from OrcaSlicer

Instead of uploading by hand, you can add a post-processing hook in OrcaSlicer
that pushes exported G-code to PrintStash after every slice, so new revisions
land in the vault automatically. Configure the hook to point at your PrintStash
upload endpoint with an API key created under **Settings**.
