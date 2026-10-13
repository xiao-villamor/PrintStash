"""Native configuration is a narrow immutable projection of resolved settings."""

import pytest

from app.core import native_settings
from app.core.config import _overlay
from app.core.config import settings as application_settings


@pytest.fixture(autouse=True)
def fresh_projection(monkeypatch):
    monkeypatch.setattr(native_settings, "_snapshot", None)


class TestCapture:
    def test_captures_runtime_overrides(self, monkeypatch):
        monkeypatch.setitem(_overlay, "model_thumbnail_width", 640)

        values = native_settings.capture()

        assert values["model_thumbnail_width"] == 640
        assert (
            values["mesh_worker_timeout_seconds"]
            == application_settings.mesh_worker_timeout_seconds
        )
        assert "jwt_secret" not in values
        assert "db_url" not in values
        assert "storage_provider_config" not in values

    def test_parent_reads_current_effective_values(self, monkeypatch):
        monkeypatch.setitem(_overlay, "model_thumbnail_width", 320)
        assert native_settings.settings.model_thumbnail_width == 320
        monkeypatch.setitem(_overlay, "model_thumbnail_width", 640)
        assert native_settings.settings.model_thumbnail_width == 640

    def test_none_override_retains_the_frozen_value(self, monkeypatch):
        monkeypatch.setitem(_overlay, "model_thumbnail_width", None)
        assert (
            native_settings.capture()["model_thumbnail_width"]
            == application_settings.frozen.model_thumbnail_width
        )


class TestInstall:
    def test_preserves_automatic_adapter_selection(self):
        values = native_settings.capture()
        values["compute_adapter"] = None
        native_settings.install(values)
        assert native_settings.settings.compute_adapter is None

    def test_keeps_a_detached_immutable_projection(self, monkeypatch):
        values = native_settings.capture()
        values["model_thumbnail_width"] = 640
        native_settings.install(values)
        values["model_thumbnail_width"] = 320
        monkeypatch.setitem(_overlay, "model_thumbnail_width", 1280)

        assert native_settings.installed()
        assert native_settings.settings.model_thumbnail_width == 640
        captured = native_settings.capture()
        captured["model_thumbnail_width"] = 96
        assert native_settings.settings.model_thumbnail_width == 640
        with pytest.raises(TypeError, match="read-only"):
            native_settings.settings.model_thumbnail_width = 32

    @pytest.mark.parametrize(
        "defect", ["missing", "extra", "nonfinite", "object", "list"]
    )
    def test_refuses_an_invalid_projection(self, defect):
        values = native_settings.capture()
        if defect == "missing":
            values.pop("model_thumbnail_width")
        elif defect == "extra":
            values["unexpected"] = 1
        elif defect == "nonfinite":
            values["mesh_memory_budget_fraction"] = float("nan")
        elif defect == "object":
            values["model_thumbnail_width"] = object()
        else:
            values = []

        with pytest.raises(ValueError, match="invalid native"):
            native_settings.install(values)
        assert not native_settings.installed()

    def test_refuses_reinstallation(self):
        values = native_settings.capture()
        native_settings.install(values)
        with pytest.raises(RuntimeError, match="already installed"):
            native_settings.install(values)

    def test_refuses_undeclared_worker_configuration(self):
        native_settings.install(native_settings.capture())
        with pytest.raises(AttributeError, match="jwt_secret"):
            _ = native_settings.settings.jwt_secret
