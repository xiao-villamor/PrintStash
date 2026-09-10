"""Remote Artifact analysis releases materialization capacity after a parser failure."""

import hashlib
import json

import pytest
from sqlmodel import select

from app.db.models import (
    CapacityReservation,
    FileType,
    GeometryFingerprint,
    ThumbnailRenderSlot,
)
from app.db.session import get_session_factory
from app.modules.media.thumbnail_engine import ThumbnailEngine
from app.modules.similarity import configuration, runs
from app.modules.similarity.processing import SimilarityProcessor
from app.modules.storage.storage_backend.s3 import S3StorageBackend
from app.modules.storage.storage_providers import (
    parse_provider_config,
    resolve_transport,
)
from tests.fixtures.storage_presets import real_preset_configuration

pytestmark = pytest.mark.s3


class TestRemoteProcessing:
    def test_releases_temporary_capacity_after_invalid_remote_mesh(
        self, db_session, local_storage, make_user, make_model, make_file, monkeypatch
    ):
        config = real_preset_configuration("s3_self_hosted")
        backend = S3StorageBackend(
            transport=resolve_transport(parse_provider_config(config))
        )
        actor = make_user(superuser=True)
        configuration.update_settings(db_session, actor, {"enabled": True})
        content = b"malformed STL input"
        file = make_file(
            make_model(),
            file_type=FileType.STL,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )
        file.path = backend.blob_key(
            file.model.slug, file.version, file.original_filename
        )
        db_session.add(file)
        db_session.commit()
        backend._client.put_object(Bucket=backend._bucket, Key=file.path, Body=content)
        materialized = []
        generate = ThumbnailEngine.generate

        def observe(engine, request):
            materialized.append(request.path)
            assert request.path.read_bytes() == content
            return generate(engine, request)

        monkeypatch.setattr(ThumbnailEngine, "generate", observe)
        try:
            run = runs.start(db_session, actor)
            assert SimilarityProcessor(get_session_factory(), backend).work_one()
            db_session.refresh(run)
            assert json.loads(run.counters_json)["failed"] == 1
            assert db_session.exec(select(GeometryFingerprint)).one().state == "failed"
            assert len(materialized) == 1
            assert not materialized[0].exists()
            assert db_session.exec(select(CapacityReservation)).all() == []
            assert all(
                row.lease_token is None
                for row in db_session.exec(select(ThumbnailRenderSlot))
            )
            assert backend.read_bytes(file.path) == content
        finally:
            backend._client.delete_object(Bucket=backend._bucket, Key=file.path)
            backend._client.delete_bucket(Bucket=backend._bucket)
