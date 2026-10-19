"""Cooldown evidence expires predictably and cannot break CPU routing."""

import pytest

from app.runtime.compute import health


class TestCooldown:
    def test_expires_device_failure_evidence(self, tmp_path, monkeypatch):
        monkeypatch.setattr(health.time, "time", lambda: 100.0)
        health.failed(tmp_path)
        assert (tmp_path / "cooldown").read_text() == "130.0"
        assert health.cooling(tmp_path)
        monkeypatch.setattr(health.time, "time", lambda: 130.0)
        assert not health.cooling(tmp_path)

    @pytest.mark.parametrize("value", ["garbage", "131", "99"])
    def test_refuses_invalid_cooldown_evidence(self, tmp_path, monkeypatch, value):
        monkeypatch.setattr(health.time, "time", lambda: 100.0)
        (tmp_path / "cooldown").write_text(value)
        assert not health.cooling(tmp_path)

    def test_tolerates_unavailable_cooldown_storage(self, tmp_path):
        absent = tmp_path / "missing" / "nested"
        health.failed(absent)
        assert not health.cooling(absent)
