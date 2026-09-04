"""Compatibility entrypoint for the shared Moonraker/Spoolman emulator."""

from printstash_core_testkit.mock_printer import (
    PUSH_INTERVAL_S,
    MockState,
    create_app,
    main,
)

__all__ = ["PUSH_INTERVAL_S", "MockState", "create_app", "main"]

if __name__ == "__main__":  # pragma: no cover - manual emulator entrypoint
    main()
