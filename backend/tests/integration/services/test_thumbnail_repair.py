"""Thumbnail repair resolves Artifact bytes independently from vault storage.

External Library paths belong to the user's NAS. A remote active vault may still
store the generated thumbnail, but it must never receive the NAS path as an object key.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlmodel import Session

from app.db.models import FileType
from app.services import thumbnail_repair
from app.services.storage_backend import get_backend
from app.services.thumbnail_engine import ThumbnailResult, ThumbnailStrategy
from tests.factories import build_file, build_model


class TestRegenerateModelThumbnail:
    def test_missing_model_returns_a_typed_failure(self, db_session: Session) -> None:
        result = thumbnail_repair.regenerate_model_thumbnail_result(
            db_session, model_id=999_999
        )

        assert result.outcome is thumbnail_repair.ThumbnailEnsureOutcome.FAILED
        assert result.failure_reason == "model_not_found"

    def test_model_without_mesh_returns_a_typed_failure(
        self, db_session: Session
    ) -> None:
        model = build_model(db_session, name="No mesh")

        result = thumbnail_repair.regenerate_model_thumbnail_result(
            db_session, model.id
        )

        assert result.outcome is thumbnail_repair.ThumbnailEnsureOutcome.FAILED
        assert result.failure_reason == "no_readable_mesh"

    def test_coalesced_generation_stops_revision_fallback(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        model = build_model(db_session, name="Coalesced repair")
        build_file(
            db_session,
            model,
            filename="newest.stl",
            file_type=FileType.STL,
        )
        calls = 0

        def coalesced(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return thumbnail_repair.ThumbnailEnsureResult(
                thumbnail_repair.ThumbnailEnsureOutcome.COALESCED,
                None,
            )

        monkeypatch.setattr(thumbnail_repair, "ensure_thumbnail", coalesced)

        result = thumbnail_repair.regenerate_model_thumbnail_result(
            db_session, model.id
        )

        assert result.outcome is thumbnail_repair.ThumbnailEnsureOutcome.COALESCED
        assert calls == 1

    def test_reads_an_external_mesh_without_sending_its_path_to_the_vault(
        self,
        db_session: Session,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        payload = b"external mesh bytes"
        source = tmp_path / "nas" / "mesh.stl"
        source.parent.mkdir()
        source.write_bytes(payload)
        model = build_model(
            db_session, name="External mesh", slug="external-mesh", hash="7" * 64
        )
        build_file(
            db_session,
            model,
            path=str(source),
            filename=source.name,
            file_type=FileType.STL,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            external=True,
        )
        real_backend = get_backend()

        class GuardedVault:
            def exists(self, key: str) -> bool:
                if key == str(source):
                    raise AssertionError(
                        "external path reached the active vault backend"
                    )
                return real_backend.exists(key)

            def __getattr__(self, name: str):
                return getattr(real_backend, name)

        monkeypatch.setattr(thumbnail_repair, "get_backend", lambda: GuardedVault())

        class FakeEngine:
            def generate(self, request):
                assert request.path.read_bytes() == payload
                return ThumbnailResult(
                    image=_png(),
                    geometry={},
                    strategy=ThumbnailStrategy.FULL,
                    complete=True,
                    failure_reason=None,
                    duration_ms=1,
                    peak_rss_bytes=1,
                )

        monkeypatch.setattr(thumbnail_repair, "ThumbnailEngine", FakeEngine)

        regenerated = thumbnail_repair.regenerate_model_thumbnail(db_session, model.id)

        assert regenerated is True

    def test_tries_the_previous_live_revision_when_the_newest_is_missing(
        self,
        db_session: Session,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        older_source = tmp_path / "older.stl"
        older_source.write_bytes(b"readable")
        model = build_model(db_session, name="Revisions")
        older = build_file(
            db_session,
            model,
            path=str(older_source),
            filename="older.stl",
            file_type=FileType.STL,
            external=True,
            size_bytes=len(b"readable"),
            sha256=hashlib.sha256(b"readable").hexdigest(),
        )
        build_file(
            db_session,
            model,
            path=str(tmp_path / "missing.stl"),
            filename="newer.stl",
            file_type=FileType.STL,
            external=True,
            sha256=hashlib.sha256(b"missing").hexdigest(),
        )

        class FakeEngine:
            def generate(self, request):
                assert request.path.read_bytes() == b"readable"
                return ThumbnailResult(
                    image=_png(),
                    geometry={},
                    strategy=ThumbnailStrategy.FULL,
                    complete=True,
                    failure_reason=None,
                    duration_ms=1,
                    peak_rss_bytes=1,
                )

        monkeypatch.setattr(thumbnail_repair, "ThumbnailEngine", FakeEngine)

        result = thumbnail_repair.regenerate_model_thumbnail_result(
            db_session, model.id
        )

        db_session.refresh(model)
        assert result.available, result
        assert model.thumbnail_file_id == older.id
        assert older.thumbnail_path is not None


def _png() -> bytes:
    import io

    from PIL import Image

    output = io.BytesIO()
    Image.new("RGBA", (32, 24), (120, 140, 200, 255)).save(output, format="PNG")
    return output.getvalue()
