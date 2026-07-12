# Printer providers

Moonraker/Klipper-first; every other provider must make unsupported actions
explicit. Support levels and per-provider behavior/safety rules:
`docs/provider-support.md` (canonical — keep it updated with the code).

## Architecture

All in `backend/app/services/`:

- `printer_provider.py` — the seam: `Capability` enum,
  `ProviderCapabilities`, `PrinterProviderClient` protocol, `BaseProvider`
  (unimplemented methods raise `ProviderError` via capability checks), and the
  `PROVIDERS` registry (subclasses self-register).
- Per-provider modules: `moonraker.py`, `prusalink.py`, `octoprint.py`,
  `elegoo_centauri.py` (+ Bambu inside `printer_provider.py`).
- `printer_hub.py` — status fan-out / WS subscriber registry (RealtimeBus).
- Variants: printers sharing a protocol use a setup preset + variant, not a
  second implementation (Elegoo Neptune 4 → Moonraker provider,
  `provider_variant` drives model-name detection).

## Adding a provider — checklist

Adding a provider means writing one class (module docstring in
`printer_provider.py` is the how-to). Then:

- [ ] Declare the enum member in `db/models.py` `PrinterProvider` + credential
      columns → new Alembic migration (see
      [backend.md](backend.md); pattern: `e2b6c9a4f7d3_octoprint_provider.py`).
- [ ] Subclass `BaseProvider`: declare `provider`, honest
      `ProviderCapabilities`, implement ONLY supported methods. Never
      advertise a capability firmware can't safely honor (Centauri file-list
      probes crash the printer daemon — that's why upload/list stay off).
- [ ] Conformance: add a `FULL_CREDENTIALS` row in
      `tests/test_provider_conformance.py` — the suite then auto-tests the
      contract (`test_every_provider_is_covered` fails until you do).
- [ ] Protocol-specific tests in a per-provider module
      (`tests/test_prusalink.py` style) against mocked transports.
- [ ] Diagnostics work without leaking secrets:
      `GET /api/v1/printers/{id}/diagnostics`.
- [ ] Frontend: add-printer flow preset asking only the credentials this
      provider needs; UI disables unsupported actions from capability flags;
      label as **beta** until hardware-validated.
- [ ] Docs: section in `docs/provider-support.md` (behavior, safety rules,
      not-supported list, smoke test) + `docs/known-limitations.md` note;
      changelog entry.

## Safety rules (non-negotiable)

- Upload never auto-starts a print; start is an explicit user action
  (Bambu additionally requires the printer to be idle).
- New providers ship as **beta** until someone runs the smoke test on real
  hardware and logs it in the Hardware Validation Log
  (`docs/provider-support.md`) — mocked-transport tests don't count as
  hardware validation, and the log is never pre-filled.
- Don't send vendor firmware updates or maintenance macros.
- Printer credentials/access codes never appear in fixtures, logs, audit
  diffs, or diagnostics responses.

## Moonraker specifics

Live status over WebSocket via `printer_hub`; print-history import is
model-scoped (matches history entries to the model's known G-code filenames,
skips already-imported). Neptune 4 family rides this provider via preset —
don't fork the Moonraker/Klipper protocol implementation.
