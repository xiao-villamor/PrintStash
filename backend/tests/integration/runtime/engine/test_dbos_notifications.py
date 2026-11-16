"""Opt-in refuses incomplete SDK patches before starting workers.

These filesystem checks use a disposable SDK-shaped directory; they never
modify the shared installed SDK. Actual queue progress is a separate contract.
"""

from types import SimpleNamespace

import pytest

from app.runtime.engine import dbos_notifications as adapter


class TestVerifyPatch:
    def test_refuses_an_unpatched_environment(self, tmp_path, monkeypatch):
        package = SimpleNamespace(__file__=str(tmp_path / "__init__.py"))
        monkeypatch.setattr(adapter.importlib, "import_module", lambda name: package)

        with pytest.raises(RuntimeError, match="missing_or_incompatible"):
            adapter.verify_patch()

    def test_refuses_a_corrupt_manifest(self, tmp_path, monkeypatch):
        package = SimpleNamespace(__file__=str(tmp_path / "__init__.py"))
        monkeypatch.setattr(adapter.importlib, "import_module", lambda name: package)
        (tmp_path / adapter.MANIFEST).write_text("{not json")

        with pytest.raises(RuntimeError, match="missing_or_incompatible"):
            adapter.verify_patch()
