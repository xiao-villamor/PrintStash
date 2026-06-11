# Printer Provider Support

PrintStash 0.1 is Moonraker/Klipper-first. Other printer providers can exist, but
they must make unsupported actions explicit in the API and UI.

## Moonraker / Klipper

Support level: stable.

Expected 0.1 behavior:

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

Expected 0.1 behavior:

- local status polling
- pause, resume, and cancel controls

Not supported in 0.1:

- upload/send from the Vault
- start remote files
- remote file inventory
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
