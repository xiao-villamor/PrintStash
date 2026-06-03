# Stage 3 — The Hub (Moonraker/Klipper Bidirectional Integration)

**Codename:** The Hub
**Status:** completed

## Goal

Add Moonraker/Klipper bidirectional integration and multi-printer farm support to
PrintStash. Users can register their 3D printers (Moonraker endpoints), send g-code
files directly from the vault to any printer, control prints (pause/resume/cancel),
monitor live printer state through the web UI, and get a farm-wide health dashboard.

---

## Execution Log

### Completed

- [x] **Moonraker HTTP client** (`backend/app/services/moonraker.py`)
  - Full HTTP wrapper: upload g-code, start/pause/resume/cancel print, one-shot status query
  - WebSocket subscription to `printer.objects` with auto-reconnect + exponential backoff
  - Error handling: `MoonrakerError` exception class, transport error wrapping
  - Reference: https://moonraker.readthedocs.io/en/latest/web_api/

- [x] **PrinterHub background worker** (`backend/app/services/printer_hub.py`)
  - One persistent WS subscription per printer
  - Live snapshot merge + DB writeback (status, last_seen_at, last_error)
  - Fan-out to vault WebSocket clients (frontend UI)
  - Lifecycle: start_all/stop_all in FastAPI lifespan, dynamic add/remove/restart
  - `_sync_active_job`: maps Moonraker state transitions to PrintJob rows
  - **External job capture**: auto-creates PrintJob rows for jobs started outside the vault

- [x] **Printer & PrintJob models** (`backend/app/db/models.py`)
  - `Printer` table: name, moonraker_url, api_key, status, last_seen_at, last_error, **group**
  - `PrintJob` table: links printer + file + model, tracks remote_filename, state, progress, **source**
  - `PrinterStatus` enum (6 states) and `PrintJobState` enum (8 states)
  - Sentinel Model/File rows for external (non-vault) print jobs

- [x] **Pydantic schemas** (`backend/app/schemas/printers.py`)
  - `PrinterCreate`, `PrinterUpdate`, `PrinterRead` (with `group` field)
  - `SendToPrinter`, `PrintJobRead` (with `source` field)

- [x] **Printers API router** (`backend/app/api/v1/printers.py`)
  - Full CRUD for printers (list, get, create, update, delete)
  - **Group filter**: `?group=garage` query param on list endpoint
  - **Farm dashboard**: `GET /dashboard` with per-status counts, active jobs, group breakdown
  - Send-to-print: upload vault file to Moonraker, optionally start print
  - Print control: pause, resume, cancel
  - One-shot status snapshot (`GET /{id}/status`)
  - Print job history (`GET /{id}/jobs`)
  - Live WebSocket status stream (`WS /{id}/ws`)

- [x] **Application integration** (`backend/app/main.py`)
  - PrinterHub instantiated + started/stopped in FastAPI lifespan
  - `get_hub` and `get_hub_from_ws` FastAPI dependencies

- [x] **Frontend UI** (`frontend/src/`)
  - Printer list page with status indicators, add/delete modal
  - Printer detail page with live WS status, progress bar, temperatures, controls
  - Klipper Sync Panel on model detail page (send-to-print)
  - Navigation entries in sidebar, bottom bar, mobile drawer
  - API client functions (12 endpoints) and TypeScript types

- [x] **Tests** — 73 tests, all passing (zero lint errors)
  - `test_moonraker.py`: 25 tests — HTTP requests, WS subscribe, error handling, URL construction
  - `test_printer_hub.py`: 20 tests — lifecycle, mark_status, handle_status, state mapping,
    sync_active_job (vault + external), get_hub dependency
  - `test_printers_api.py`: 28 tests — CRUD, send-to-print, controls, status, jobs,
    dashboard, group filter

- [x] **Documentation**
  - `docs/stage3.md` — this file

### Carried into Stage 4

- Provider abstraction (`moonraker`, `bambu_lan`) now implemented in Stage 4f
- API compatibility preserved while adding capability-aware provider dispatch
- Future enhancements remain:
  - Redis pub/sub for multi-process printer hub
  - Load balancing: auto-select least-busy printer
  - Printer health alerting: email/webhook on errors
