---
title: Printers & providers
description: Moonraker/Klipper and Bambu LAN support levels and what each can do.
---

PrintStash is Moonraker/Klipper-first. Other providers can exist, but they must
make unsupported actions explicit in both the API and the UI.

## Moonraker / Klipper

**Support level: stable.**

Current behavior:

- Live printer status over WebSocket
- Upload vault G-code to Moonraker
- Optionally start a just-uploaded file
- Pause, resume, and cancel active prints
- Sync remote G-code file inventory
- Start an already-present remote G-code file
- Import matching print-history entries into a model's print history

### Smoke test

1. Register a Moonraker printer with its reachable LAN URL.
2. Open the printer detail page and verify status changes.
3. Sync printer files.
4. Send a small known-good G-code file without auto-start.
5. Start, pause, resume, and cancel only on a printer where that is safe.

## Bambu LAN

**Support level: beta.**

Current behavior:

- Local status polling
- Pause, resume, and cancel controls

Not supported today:

- Upload/send from the vault
- Start remote files
- Remote file inventory
- Cloud printer control

The API exposes this through provider capabilities and diagnostics. The UI
labels Bambu LAN as beta and disables unsupported actions.

## Diagnostics

```bash
curl http://localhost:8000/api/v1/printers/<printer-id>/diagnostics
```

The response reports provider support level, capabilities, unsupported actions,
configuration checks, and live-status connectivity checks — without returning
stored secrets.

## Model-level history import

Moonraker print-history import is model-scoped. PrintStash matches recent
Moonraker history entries to the model's known G-code filenames, records new
matches as `printer_history` jobs, and skips already-imported remote filenames.
