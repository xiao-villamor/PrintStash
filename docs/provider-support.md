# Printer Provider Support

PrintStash is Moonraker/Klipper-first. Other printer providers can exist, but
they must make unsupported actions explicit in the API and UI.

## Moonraker / Klipper

Support level: stable.

Current behavior:

- live printer status over WebSocket
- upload Vault G-code to Moonraker
- optionally start a just-uploaded file
- pause, resume, and cancel active prints
- sync remote G-code file inventory
- start an already-present remote G-code file
- import matching print-history entries into a model's print history

Recommended smoke test:

1. Register a Moonraker printer with its reachable LAN URL.
2. Open the printer detail page and verify status changes.
3. Sync printer files.
4. Send a small known-good G-code file without auto-start.
5. Start, pause, resume, and cancel only on a printer where that is safe.

## Bambu LAN

Support level: beta.

Current behavior:

- local status polling
- upload plain-text Vault G-code over LAN FTPS
- explicitly start a just-uploaded G-code file; upload alone never starts it
- pause, resume, and cancel controls

Safety rules:

- PrintStash checks that the printer is idle before a Bambu Vault send.
- Start requires the user to select **Start print immediately** in the send
  dialog; the default is upload-only.
- Upload and start remain beta until validated against more firmware versions.

Not supported:

- remote file inventory
- delete remote files
- raw G-code controls
- measured filament consumption
- cloud printer control

The API exposes this through provider capabilities and diagnostics. The UI labels
Bambu LAN as beta and disables unsupported actions.

## PrusaLink

Support level: beta.

PrintStash connects directly to PrusaLink on the local network; it does not
send printer credentials or jobs through Prusa Connect cloud. Supported:

- HTTP Digest username/password authentication on modern PrusaLink
- legacy `X-Api-Key` authentication
- polled printer/job status and temperatures
- Vault G-code upload and explicit start
- remote G-code inventory and deletion
- pause, resume, and cancel controls

Raw G-code commands and measured filament consumption are not supported.
Physical-printer coverage is still expanding, so supervise first prints after
adding or updating a PrusaLink device.

## OctoPrint

Support level: beta.

PrintStash connects to an OctoPrint/OctoPi instance on the local network
using an `X-Api-Key`. Supported:

- polled printer/job status and temperatures
- G-code upload and explicit start of an existing file
- remote G-code inventory and deletion
- pause, resume, and cancel controls

Raw G-code commands and measured filament consumption are not supported.
Physical-printer coverage is still expanding, so supervise first prints after
adding or updating an OctoPrint device.

## Elegoo Neptune 4 family

Neptune 4, 4 Pro, 4 Plus, and 4 Max use the Moonraker provider through a
dedicated setup preset. This intentionally avoids a second implementation of
the same Klipper/Moonraker protocol. PrintStash does not install or update
Elegoo firmware and sends no vendor-specific maintenance macros.

Other Elegoo families are not implied by this Neptune preset; native Centauri
support is documented separately below.

## Elegoo Centauri Carbon

Support level: beta.

- Original Centauri Carbon: local SDCP v3 over WebSocket port 3030; no
  authentication. Saving the mainboard ID is recommended because some firmware
  states do not announce it while paused or errored.
- Centauri Carbon 2: authenticated local MQTT on port 1883. Enable **LAN Only**
  in printer network settings and enter the access code shown by the printer.
- Both models: live status, temperatures, progress, start of a file already on
  printer storage, pause, resume, and cancel.

Upload, file inventory/deletion, raw G-code, and measured consumption are not
advertised. Original Carbon file-list probes can terminate its printer daemon;
Carbon 2's documented file-list request does not answer on validated firmware.
PrintStash therefore never probes those operations.

## Hardware Validation Log

Automated tests cover protocol logic against mocked transports; they don't
prove a given printer model/firmware combination actually behaves this way.
Entries below are added only after someone runs the relevant smoke test (see
per-provider sections above, and the Printers section of
[`manual-testing.md`](./manual-testing.md)) against real hardware — this is
not a checklist to pre-fill.

| Provider | Model | Firmware | Date | Tester | Result | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| _(none yet)_ | | | | | | |

Before tagging a release that touches a provider, either add a row here from
a real test or carry forward the "still needs real-world hardware
validation" note in `docs/known-limitations.md` — don't leave it silently
implied as done.

## Diagnostics

Use:

```bash
curl http://localhost:8000/api/v1/printers/<printer-id>/diagnostics
```

The response reports provider support level, capabilities, unsupported actions,
configuration checks, and live-status connectivity checks without returning stored
secrets.

## Model-Level History

Moonraker print-history import is model-scoped. PrintStash matches recent
Moonraker history entries to the model's known G-code filenames, records new
matches as `printer_history` jobs, and skips already-imported remote filenames.
