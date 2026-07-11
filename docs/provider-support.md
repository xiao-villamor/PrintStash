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
