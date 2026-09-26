"""Root probes report measured publication support without leaving probe files.

A failed probe is diagnostic evidence, never permission to replace an object or
silently upgrade a provider's identity guarantees.
"""

from __future__ import annotations

import errno
import os

from app.modules.storage.storage_backend.contracts import LocalRootRole
from app.modules.storage.storage_backend.probes import probe_local_root


class TestProbeLocalRoot:
    def test_reports_a_missing_root(self, tmp_path):
        probe = probe_local_root(LocalRootRole.STAGING, tmp_path / "missing")
        assert not probe.hardlink
        assert not probe.exclusive_create
        assert "write permissions" in probe.warnings[0]
        assert not (tmp_path / "missing").exists()

    def test_reports_exclusive_creation_failure(self, tmp_path, monkeypatch):
        real_open = os.open

        def deny_probe(path, *args, **kwargs):
            if ".printstash-exclusive-probe-" in str(path):
                raise OSError(errno.EACCES, "denied")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(os, "open", deny_probe)
        probe = probe_local_root(LocalRootRole.BACKUP, tmp_path)
        assert probe.hardlink
        assert not probe.exclusive_create
        assert list(tmp_path.iterdir()) == []

    def test_reports_unavailable_directory_sync(self, tmp_path, monkeypatch):
        def fail_sync(fd):
            raise OSError(errno.EINVAL, "directory sync unavailable")

        monkeypatch.setattr(os, "fsync", fail_sync)
        probe = probe_local_root(LocalRootRole.STAGING, tmp_path)
        assert probe.hardlink
        assert not probe.directory_fsync
        assert list(tmp_path.iterdir()) == []
