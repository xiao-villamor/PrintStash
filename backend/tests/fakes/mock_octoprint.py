"""Compatibility entrypoint for the shared OctoPrint emulator."""

from printstash_core_testkit.mock_octoprint import create_app, main

__all__ = ["create_app", "main"]

if __name__ == "__main__":  # pragma: no cover - manual emulator entrypoint
    main()
