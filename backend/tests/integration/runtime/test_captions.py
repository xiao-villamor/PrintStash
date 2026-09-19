"""A failed caption work unit cannot terminate the optional scheduler."""

import asyncio
import threading

import pytest

from app.runtime import captions


class TestRunCaptions:
    @pytest.mark.asyncio
    async def test_resumes_after_a_failed_unit(self, monkeypatch, caplog):
        recovered = threading.Event()
        attempts = 0

        def unit():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("private provider payload")
            recovered.set()
            return False

        monkeypatch.setattr(captions, "process_one", unit)
        task = asyncio.create_task(captions.run_captions())
        try:
            assert await asyncio.to_thread(recovered.wait, 8)
            assert not task.done()
            assert "Caption work paused" in caplog.text
            assert "private provider payload" not in caplog.text
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
