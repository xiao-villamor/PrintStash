# Clean Demo Walkthrough

Use this flow for screenshots, a short GIF, a README demo, or a first community
post. It is designed to show PrintStash's current value without promising features
that are still future work.

## Goal

Show that PrintStash can answer:

- What did I slice?
- Which G-code revision was the good one?
- What slicer/material/printer settings were used?
- What does the sliced toolpath look like?
- Can I find it again later?
- Can I see whether a printer/provider is ready?
- Can I export my metadata?

## Setup

1. Start the app:

   ```bash
   cp .env.example .env
   docker compose up -d --build
   ```

2. Open `http://localhost:3000/setup` and create the first admin.
3. Sign in with the admin account created during setup.
4. Prepare one safe STL or 3MF and one safe G-code file from OrcaSlicer,
   PrusaSlicer, Bambu Studio, or Cura.

Do not use private customer models, production files, access codes, serials,
printer hostnames, or screenshots that reveal private network details.

## Demo Script

### 1. Library First Impression

- Open the model grid.
- Show categories, tags, search, and the empty or populated library state.
- Upload one STL/3MF and assign a category such as `Functional/Brackets`.
- Show the generated thumbnail and model card.

### 2. Metadata Extraction

- Upload one G-code file for the same model.
- Open the model detail page.
- Point at slicer metadata: slicer, layer height, infill, material, estimated
  time, filament usage, and printer model where available.
- Open the mesh viewer if the model has an STL, 3MF, or OBJ file.
- Toggle to the G-code toolpath viewer, scrub layers, and show travel/bed
  overlays when available.

### 3. Revision Story

- Add a second G-code revision or edit revision fields.
- Mark one revision as `needs_test`, `known_good`, or `failed`.
- Add a short note such as `PETG baseline` or `Tighter fit`.
- Mark the best file as recommended.
- Add or import a print-history entry for the recommended revision if a safe
  Moonraker printer or disposable test data is available.

### 4. Find It Again

- Return to the grid.
- Search by model name.
- Filter by category/tag.
- Show that the model keeps files and revisions together.

### 5. Provider Diagnostics

- If a Moonraker printer is available, add it and open the printer detail page.
- Open the Diagnostics tab.
- Show provider support level, capability checks, and unsupported actions.
- If no printer is available, use this section to explain that PrintStash is
  Moonraker-first and Bambu LAN is currently beta/status-control-only.

### 6. Data Portability

- Open Settings.
- Use Data export to download JSON and CSV.
- Show vault stats, storage usage, and model-card metric choices.
- Explain that exports are metadata-only and require auth because filenames,
  materials, and print history can still be sensitive.

## What Not To Claim

- Do not claim PrintStash is a slicer.
- Do not claim Bambu LAN upload/send/start parity.
- Do not claim full print-farm scheduling.
- Do not claim CNC/laser/vinyl/PCB support yet.
- Do not claim public cloud hosting.

## Screenshot Checklist

- Asset grid with at least one populated model card.
- Model detail showing files, metadata, and revisions.
- Mesh viewer with a safe model.
- G-code toolpath viewer with safe non-private G-code.
- Search or category filter.
- Setup wizard or login screen.
- Printer diagnostics if a safe provider is available.
- Settings Data export card.
