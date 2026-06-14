---
title: Printers & providers
description: What Moonraker/Klipper and Bambu LAN can do, and how to connect them.
---

PrintStash is Moonraker/Klipper-first, and the design has an opinion about it:
other providers are welcome, but any action a provider *can't* do has to be
explicit — in the API capabilities, in diagnostics, and in the UI, which disables
what isn't supported rather than letting you click into a failure. So you'll never
wonder whether "send to printer" silently did nothing; if it's greyed out, the
provider doesn't support it.

Behind the scenes, a component called the **PrinterHub** keeps each provider's
status in memory, writes a coarse snapshot to the database, and fans live updates
out to the browser over WebSocket. That's what powers the live status badge and
the reconnecting indicator on the printer detail page.

## Moonraker / Klipper — stable

This is the fully-supported path. A connected Moonraker printer gives you:

- Live status over WebSocket — state, current file, progress, elapsed/total
  time, hotend and bed temperatures.
- Upload a vault G-code file to the printer, optionally starting it immediately.
- Pause, resume, and cancel the active print.
- Sync the printer's remote G-code inventory and start a file that's already
  there.
- Import the printer's print history onto the matching models.

### Adding one

From **Printers → Add printer**, choose Moonraker/Klipper and give it a name and
its reachable LAN URL (the address Mainsail/Fluidd lives at). Save, open the
detail page, and you should see the status badge go live within a few seconds.

### Smoke test before you trust it

Do this once on a printer where a mistake is harmless:

1. Register the printer with its LAN URL.
2. Open the detail page and confirm the status changes as the printer does.
3. Sync printer files and check the inventory looks right.
4. Send a small known-good G-code file **without** auto-start.
5. Only then exercise start / pause / resume / cancel — and only where doing so
   can't damage anything.

## Bambu LAN — beta

Local Bambu support exists, but it's intentionally narrow for now:

**Works:** local status polling, plus pause / resume / cancel.

**Not supported yet:**

- Uploading or sending files from the vault
- Starting remote files
- Remote file inventory
- Any cloud-based control

The UI labels Bambu LAN as beta and disables the actions above. Upload/send
parity is on the roadmap, not in the current release — so don't build a workflow
that depends on it.

## Diagnostics

Every printer has a **Diagnostics** tab, and the same data is available over the
API:

```bash
curl http://localhost:8000/api/v1/printers/<printer-id>/diagnostics
```

It reports the provider's support level, capability checks, the list of
unsupported actions, configuration checks, and live connectivity checks — and it
does *not* return stored secrets, so it's safe to share when asking for help. If
a printer won't connect, this tab is the first place to look: it'll tell you
whether it's a config problem or a reachability problem.

## Importing print history

History import is **model-scoped** and conservative. When you import on a model,
PrintStash matches recent Moonraker history entries against that model's known
G-code filenames, records new matches as `printer_history` jobs, and skips any
remote filename it has already imported. So you can re-run it without piling up
duplicate entries.

## When a printer won't behave

- **Status badge stuck on "reconnecting."** The browser can't reach the
  WebSocket. Confirm `NEXT_PUBLIC_WS_URL` points at an address reachable from
  your browser (not just from inside the container) — see
  [Configuration](/PrintStash/getting-started/configuration/#frontend).
- **Printer shows offline but Mainsail works.** Check the URL you registered is
  reachable from the PrintStash *server*, and run Diagnostics for the specific
  failing check.
- **An action you expected is greyed out.** That's the explicit-capabilities
  design at work — the provider doesn't support it. Diagnostics lists exactly
  which actions are unsupported for that provider.
