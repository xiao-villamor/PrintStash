# Community Starter Issues

These are release-ready GitHub issue drafts for early contributors. They are
intentionally small, useful, and aligned with the local-first project scope.

## 1. Add Safe Parser Fixtures From Real Slicer Output

Labels: `good first issue`, `parser`, `metadata`

### Context

PrintStash extracts metadata from G-code comments, but slicers and profiles vary.
More safe fixture files improve parser confidence without requiring contributors
to understand the full app.

### Task

Add one sanitized G-code fixture from a real slicer/profile and assert the parser
extracts the expected fields.

### Acceptance Criteria

- Fixture contains no private customer names, hostnames, serials, network paths,
  access codes, or proprietary model content.
- Test covers at least two extracted fields, such as slicer name, layer height,
  material, estimated time, filament weight, or printer model.
- Existing parser tests still pass.

### Technical Notes

- Existing fixtures live in `backend/tests/fixtures/`.
- Parser tests live in `backend/tests/test_gcode_parser.py`.
- Prefer a small cropped header/footer sample if the full G-code is huge.

## 2. Document A Real Docker/NAS Install

Labels: `good first issue`, `docs`, `deployment`

### Context

Early users will run PrintStash on NAS boxes, mini PCs, and homelab servers.
Real install notes are more useful than generic deployment claims.

### Task

Add a short deployment note for one real environment.

### Acceptance Criteria

- Documents OS/device, Docker version, storage path choice, and any reverse
  proxy or WebSocket settings needed.
- Includes any gotchas encountered during first-run setup.
- Does not include private URLs, tokens, API keys, public IPs, or printer access
  codes.

### Technical Notes

- Add as a new doc under `docs/deployments/`.
- Link it from `docs/known-limitations.md` or `README.md` only if it is polished.

## 3. Validate One Moonraker Hardware Setup

Labels: `provider`, `hardware`, `help wanted`

### Context

Moonraker/Klipper is the stable provider, but hardware validation across real
setups is still the most valuable feedback.

### Task

Test PrintStash with one Moonraker printer and report provider diagnostics,
send-to-print behavior, controls, and file inventory sync.

### Acceptance Criteria

- Notes printer/firmware setup at a safe level, for example printer class,
  Klipper/Moonraker version, and UI stack.
- Confirms which actions work: live status, upload/send, start, pause, resume,
  cancel, file inventory sync.
- Captures any errors from the Diagnostics tab without exposing secrets.

### Technical Notes

- Provider diagnostics are available at `/api/v1/printers/{id}/diagnostics` and
  in the printer detail UI.
- Small known-good G-code should be used for testing.

## 4. Improve Unsupported Provider UI Copy

Labels: `ui`, `provider`, `good first issue`

### Context

Some actions are unavailable depending on the provider. Bambu LAN is beta and
currently status/control-only. The UI should make unavailable actions feel explicit
rather than broken.

### Task

Audit printer pages and model send-to-printer flows for unsupported provider
states, then tighten copy or disabled button tooltips.

### Acceptance Criteria

- Disabled actions explain whether auth, provider support, or printer state is
  the blocker.
- Bambu LAN upload/send/start/list-files limitations are clear.
- No new provider capability is implied unless it exists in the API.

### Technical Notes

- Capabilities are exposed through `PrinterRead.capabilities`.
- Diagnostics details are exposed through `PrinterDiagnostics`.

## 5. Add A Small Demo Dataset Or Demo Checklist

Labels: `docs`, `demo`, `good first issue`

### Context

New users should be able to understand the value of PrintStash without wiring up
a printer first.

### Task

Improve the demo path by adding either safe sample files or a clearer checklist
for showing upload, metadata, revision history, search, and export.

### Acceptance Criteria

- Demo requires no printer connection.
- Demo shows metadata extraction, model detail, revision labels, search/filter,
  and metadata export.
- Any sample files are safe to redistribute under the project license or clearly
  documented with their license.

### Technical Notes

- The current demo walkthrough lives in `docs/demo-walkthrough.md`.
- Screenshots live in `screenshots/`.
