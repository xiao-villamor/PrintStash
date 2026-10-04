"""Local resource composition must not require a Linux proc filesystem."""

from pathlib import Path

from app.bootstrap import native_resources
from app.runtime.native_admission import Resources


class TestConfigure:
    def test_works_without_linux_procfs(self, tmp_path, monkeypatch):
        native, prepared = [], []
        monkeypatch.setattr(native_resources, "bind_pool", native.append)
        monkeypatch.setattr(native_resources, "bind_pools", prepared.append)
        original = Path.read_text

        def read(path, *args, **kwargs):
            if str(path).startswith("/proc/"):
                raise FileNotFoundError("procfs unavailable")
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", read)
        native_resources.configure(tmp_path)
        amount = Resources(1, 100)
        with native[0].reserve(amount, amount, checkpoint=lambda: None) as permit:
            assert permit.resources == amount
        with prepared[0].prepared.reserve(
            amount, amount, checkpoint=lambda: None
        ) as permit:
            assert permit.resources == amount
