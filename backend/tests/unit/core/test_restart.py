"""A restart request must enter uvicorn's graceful shutdown path.

The deployment supervisor owns relaunching the process. PrintStash only sends
SIGTERM to itself so lifespan cleanup can close workers, clients, and locks.
"""

from __future__ import annotations

import os
import signal

from app.core import restart


class TestRequestRestart:
    def test_sends_sigterm_to_the_current_process(self, monkeypatch) -> None:
        sent: list[tuple[int, signal.Signals]] = []
        monkeypatch.setattr(
            restart.os, "kill", lambda pid, sig: sent.append((pid, sig))
        )

        restart.request_restart()

        assert sent == [(os.getpid(), signal.SIGTERM)]
