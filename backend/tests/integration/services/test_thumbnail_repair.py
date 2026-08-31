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
from tests.factories import build_file, build_model


class TestRegenerateModelThumbnail:
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
        monkeypatch.setattr(
            thumbnail_repair.mesh_processing,
            "render_thumbnail",
            lambda path: path.read_bytes(),
        )
        monkeypatch.setattr(thumbnail_repair.thumbnail, "to_webp", lambda data: data)

        regenerated = thumbnail_repair.regenerate_model_thumbnail(db_session, model.id)

        assert regenerated is True
