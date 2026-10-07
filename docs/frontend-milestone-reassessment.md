# M5–M8 closure reassessment

2026-10-07. Original source checkpoint: `c74b9aef`. This is a requirements-to-source
reassessment, not a new test run or a full reread of every frontend file.

The previous M7/M8 closure statements were too broad. Their focused suites prove
implemented behaviors, but do not waive missing cross-cutting requirements. The
plan's **Conditional edits** section requires the same conflict policy as editable
settings/printer forms migrate. Recording an unprotected write as a limitation
does not authorize dropping that requirement.

| Milestone | Evidence checked | Current assessment |
| --- | --- | --- |
| M5 | Plan row; closure and linked matrices; current bounded/session-scoped `features/library/navigation-state.ts` and restoration owner | Existing local acceptance retained. No contradictory requirement found in this reassessment; no new browser run claimed. |
| M6 | Plan row; asset/decoded-image/selection matrix; measured comparison; current four-download/400-idle/32MiB lease limits | Existing local acceptance retained for its stated scenarios. Unmeasured CPU/GPU/mobile/load scenarios remain disclosed and do not become measured by this review. |
| M7 | Async closure, owner ledger and named reconnect/offline/visibility/printer-switch cases; current printer settings write path | Async ownership work remains implemented. The reopened printer settings gap is now corrected and qualified in the [M7 matrix](frontend-m7-validation.md); local acceptance restored. |
| M8 | Plan row, closure record, Profiles/Search owners and their frontend/backend update paths | Locally accepted after correction. Profile edits are now qualified by the [preset matrix](frontend-m8-profile-edit-validation.md); Search conditional editing is now qualified by the [Search matrix](frontend-m8-search-edit-validation.md). |

## Gaps at the original checkpoint

- Printer settings: `components/printer-detail.tsx` calls
  `lib/api/printers.ts::updatePrinter` without a captured edit precondition.
  `backend/app/api/v1/printers.py::update_printer` changes fields directly without
  an atomic expected-version comparison. Do not add versions to live telemetry;
  the editable settings aggregate needs its own explicit contract.
- Filament and printer Profiles: `lib/queries/profiles.ts` owns publication and
  preserves local input, but the two update clients send unconditional PATCHes.
  `backend/app/api/v1/filaments.py::update_filament_profile` and
  `printer_profiles.py::update_printer_profile` likewise accept no editing base.
- Search configuration: `lib/api/search.ts::saveSearchSettings` sends a whole
  settings PUT without an editing base; `api/v1/inference.py::update_settings`
  delegates an unconditional update. The M8 record already admitted this missing
  protocol while incorrectly declaring the milestone closed.

These were source-confirmed contract gaps at the checkpoint. The subsequent M7
increment reproduced and corrected printer conflicts; its matrix records current
frontend/backend/browser evidence. Profiles now have their [qualified correction](frontend-m8-profile-edit-validation.md). Search now has its [qualified correction](frontend-m8-search-edit-validation.md).

## Required regression matrix before corrections

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| C1 | rejects a competing printer settings edit | Error | Two editors use the same settings base | Second write refused; retained draft and authorized review | Integration / Playwright | ✅ [M7 PE1–PE34](frontend-m7-validation.md#reopened-printer-settings-contract) |
| C2 | rejects a competing filament profile edit | Error | Two editors use the same local preset base | No last-writer overwrite; explicit revised save | Integration / Playwright | ✅ [Profile P1/P12/P20](frontend-m8-profile-edit-validation.md) |
| C3 | rejects a competing printer profile edit | Error | Two editors use the same preset base | No last-writer overwrite; explicit revised save | Integration / Playwright | ✅ [Profile P1/P12/P20](frontend-m8-profile-edit-validation.md) |
| C4 | reviews a competing Search settings edit | Error | Two settings forms use the same base | Full settings PUT cannot overwrite unnoticed | Integration / Playwright | ✅ [Search S1–S27](frontend-m8-search-edit-validation.md) |

The implementation matrices must also cover legacy compatibility, session
retirement, failed/uncertain responses and background writers before closure.

## Corrected execution order

M7 is locally accepted after the competing-edit correction and its focused
qualification. M8 is locally accepted: the earlier Library-surface evidence,
Profile correction and Search conditional-edit matrix now cover its reopened
contracts. M9 is next; its reviewed-root contract is already preserved in
`6050a375` and is not counted as M7/M8 acceptance.
M10/M11 remain pending. Final delivery and required CI are still open.
