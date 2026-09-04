#!/usr/bin/env bash
# Launch the standalone Moonraker + Spoolman emulator (backend/tests/fakes/
# mock_printer.py) so fleet.spec.ts can add a real, live printer and watch it
# come online, queue work, and print — without physical hardware. Prints
# simulate fast (a couple of seconds) so the queue/dispatch flow settles well
# inside Playwright's timeouts.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../../../../backend" && pwd)"
PORT="${PLAYWRIGHT_MOCK_PRINTER_PORT:-7530}"

cd "$BACKEND_DIR"
if [ -x .venv/bin/python ]; then
  PY=(.venv/bin/python)
else
  PY=(uv run python)
fi

exec "${PY[@]}" -m tests.fakes.mock_printer --port "$PORT" --print-seconds 3
