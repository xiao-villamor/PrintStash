"""A truncated first read must fail before remote indexing publishes any catalog data."""

from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace

import pytest
from sqlmodel import Session, select

from app.db.models import File, LibrarySourceKind, Model, OwnedStorageObject
from app.services import external_library
from app.services.library_source import (
    LibrarySourceError,
    RemoteLibrarySource,
    SourceEntry,
)
from app.services.storage_opendal import OpenDALStorageBackend
from app.services.storage_providers import TransportKind, TransportSpec
from tests.paths import FIXTURES_DIR


class TruncatedOperator:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def capability(self) -> SimpleNamespace:
        return SimpleNamespace()

    def exists(self, key: str) -> bool:
        return True

    def stat(self, key: str) -> SimpleNamespace:
        return SimpleNamespace(content_length=len(self.payload) + 1, etag="stable")

    def open(self, key: str, mode: str) -> BytesIO:
        return BytesIO(self.payload)


class TestIndexRemoteFile:
    def test_rejects_truncated_content_before_catalog_publication(
        self,
        db_session: Session,
        make_external_library,
    ) -> None:
        library = make_external_library(
            "",
            source_kind=LibrarySourceKind.WEBDAV,
            source_prefix="models",
            root_identity=None,
        )
        payload = (FIXTURES_DIR / "sample.gcode").read_bytes()
        source = RemoteLibrarySource(
            OpenDALStorageBackend(
                TransportSpec(
                    kind=TransportKind.WEBDAV,
                    provider="webdav",
                    namespace="root",
                    options={"root": "root"},
                ),
                operator=TruncatedOperator(payload),
            )
        )
        initial_model_ids = db_session.exec(select(Model.id)).all()
        initial_file_ids = db_session.exec(select(File.id)).all()
        initial_owned_ids = db_session.exec(select(OwnedStorageObject.id)).all()

        with pytest.raises(LibrarySourceError, match="library_source_size_mismatch"):
            external_library._index_remote_file(
                db_session,
                library,
                source,
                SourceEntry("models/sample.gcode", len(payload) + 1),
            )

        assert db_session.exec(select(Model.id)).all() == initial_model_ids
        assert db_session.exec(select(File.id)).all() == initial_file_ids
        assert db_session.exec(select(OwnedStorageObject.id)).all() == initial_owned_ids
