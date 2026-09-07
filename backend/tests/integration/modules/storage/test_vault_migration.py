"""Verified migration isolation and generation contracts."""

from pathlib import Path

from app.core.config import _overlay
from app.modules.storage.storage_backend.local import LocalStorageBackend


def test_local_backend_retains_its_roots_when_configuration_changes(tmp_path: Path):
    source = tmp_path / "source"
    thumbs = tmp_path / "thumbs"
    _overlay.update(data_dir=source, thumb_dir=thumbs)
    backend = LocalStorageBackend()
    _overlay.update(
        data_dir=tmp_path / "destination", thumb_dir=tmp_path / "new-thumbs"
    )
    assert backend.blob_key("model", 1, "part.stl") == str(source / "model/v1/part.stl")
    assert backend.thumbnail_key(1) == str(thumbs / "1.webp")
    assert backend.namespace_for(str(source / "part.stl")) == f"data:{source}"


def test_candidate_local_backend_never_changes_active_settings(tmp_path: Path):
    from app.modules.storage.storage_backend.factory import build_configured_backend
    from app.modules.storage.storage_providers import parse_provider_config

    _overlay.update(data_dir=tmp_path / "active", thumb_dir=tmp_path / "active-thumbs")
    candidate = build_configured_backend(
        parse_provider_config(
            {
                "provider": "local",
                "data_dir": str(tmp_path / "candidate"),
                "thumb_dir": str(tmp_path / "candidate-thumbs"),
            }
        )
    )
    assert candidate.blob_key("model", 1, "part.stl") == str(
        tmp_path / "candidate/model/v1/part.stl"
    )
    assert _overlay["data_dir"] == tmp_path / "active"
    assert _overlay["thumb_dir"] == tmp_path / "active-thumbs"


def test_reader_keeps_source_adapter_after_activation(tmp_path: Path):
    from app.modules.storage.storage_backend import generations
    from app.modules.storage.storage_backend.runtime import bind_backend, get_backend

    source = LocalStorageBackend(
        data_dir=tmp_path / "source", thumb_dir=tmp_path / "thumbs"
    )
    destination = LocalStorageBackend(
        data_dir=tmp_path / "destination", thumb_dir=tmp_path / "new-thumbs"
    )
    bind_backend(source)
    pinned = generations.pin()
    with generations.use(pinned):
        assert get_backend() is source
        pinned.planned()
        with generations.activation():
            generations.publish(destination, "migration-1")
        assert get_backend() is source
    assert get_backend() is destination
    assert generations.current_epoch() == "migration-1"


def test_activation_cannot_mix_an_unplanned_reader(tmp_path: Path):
    import pytest

    from app.modules.storage.storage_backend import generations
    from app.modules.storage.storage_backend.runtime import bind_backend, get_backend

    source = LocalStorageBackend(
        data_dir=tmp_path / "source", thumb_dir=tmp_path / "thumbs"
    )
    bind_backend(source)
    pinned = generations.pin()
    try:
        with pytest.raises(TimeoutError, match="reader_drain"):
            with generations.activation(timeout=0):
                raise AssertionError("must not enter activation")
        assert get_backend() is source
    finally:
        pinned.planned()


def test_retention_exemption_is_exactly_one_candidate(tmp_path: Path):
    import pytest

    from app.core.errors import OperationError
    from app.runtime.maintenance import (
        allow_retained_destination_destruction,
        guarded_storage_destruction,
        retain_storage_objects,
    )

    class Endpoint:
        @guarded_storage_destruction
        def remove(self):
            return "removed"

    source, candidate, foreign = Endpoint(), Endpoint(), Endpoint()
    with retain_storage_objects():
        with allow_retained_destination_destruction(
            source=source, destination=candidate
        ):
            assert candidate.remove() == "removed"
            for protected in (source, foreign):
                with pytest.raises(OperationError, match="storage_snapshot_retained"):
                    protected.remove()
        with pytest.raises(OperationError, match="storage_snapshot_retained"):
            candidate.remove()
