"""``persist_artifact`` writes one artifact, or nothing at all.

It used to commit the File row before writing the thumbnail and the Metadata
row. A failure in between (a corrupt image, a full disk) left a committed File
with no metadata — a model that renders but has no print time, filament or cost,
and no error anywhere to explain it.

Thumbnails are no longer part of the commit at all: they are derivatives,
published later by their own job (``tests/integration/modules/media/
test_thumbnail_publication.py``). What remains here is that the commit is
bare, atomic, and hands the Artifact to the derivative source.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import pytest
from printstash_core.mesh.measurements import (
    VolumeLegacyUnassessed,
    VolumeMeasured,
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
    VolumeUnavailable,
    VolumeUnavailableCause,
    encode_volume,
)
from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import _overlay
from app.db.models import (
    File,
    FileType,
    JobKind,
    Metadata,
    Model,
    ModelProvenanceField,
    ProvenanceCapture,
    ReconcileCursor,
)
from app.db.session import (
    SQLiteSessionFactory,
    _set_sqlite_pragmas,
    get_session_factory,
)
from app.modules.ingestion import ingestion
from app.modules.library import provenance
from app.modules.storage.storage_backend.contracts import StorageConfigurationError
from app.modules.storage.storage_backend.local import LocalStorageBackend
from app.modules.storage.storage_backend.runtime import get_backend
from tests.factories import (
    build_external_library,
    build_file,
    build_model,
    build_user,
)


@pytest.fixture
def storage(tmp_path: Path):
    _overlay["storage_backend"] = "local"
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    for role, root in (("data", tmp_path / "files"), ("thumb", tmp_path / "thumbs")):
        root.mkdir()
        (root / ".printstash-storage-root.json").write_text(
            json.dumps({"format": 1, "installation": "a" * 64, "role": role}),
            encoding="utf-8",
        )
    yield get_backend()
    for key in ("storage_backend", "data_dir", "thumb_dir"):
        _overlay.pop(key, None)


@pytest.fixture
def model(db_session: Session) -> Model:
    model = build_model(db_session, name="Bracket", slug="bracket", hash="h" * 64)
    return model


def _staged(tmp_path: Path, name: str = "bracket.stl") -> Path:
    staged = tmp_path / name
    staged.write_bytes(b"solid bracket\nendsolid\n")
    return staged


def _persist(db_session: Session, model: Model, staged: Path, **kwargs):
    defaults = dict(
        model=model,
        staged_path=staged,
        original_filename=staged.name,
        file_type=FileType.STL,
        blob_hash="b" * 64,
        meta={"estimated_time_s": 120},
    )
    defaults.update(kwargs)
    return ingestion.persist_artifact(db_session, **defaults)


class TestPersistArtifact:
    def test_external_writeback_rejects_replaced_root_then_retries_without_duplicate(
        self,
        db_session: Session,
        storage,
        model: Model,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        nas = tmp_path / "nas"
        nas.mkdir()
        library = build_external_library(db_session, nas)
        staged = _staged(tmp_path)
        original = staged.read_bytes()
        destination = nas / "bracket.stl"
        old_root = tmp_path / "old-nas"
        original_open = LocalStorageBackend._open_pinned_parent

        def replace_before_publication(adapter, path):
            nas.rename(old_root)
            nas.mkdir()
            return original_open(adapter, path)

        try:
            with monkeypatch.context() as patch:
                patch.setattr(
                    LocalStorageBackend,
                    "_open_pinned_parent",
                    replace_before_publication,
                )
                with pytest.raises(StorageConfigurationError):
                    _persist(
                        db_session,
                        model,
                        staged,
                        is_external=True,
                        external_library_id=library.id,
                        dest_key_override=str(destination),
                        ingestion_key="issue-210-root-replacement",
                        blob_hash=hashlib.sha256(original).hexdigest(),
                    )
            assert not destination.exists()
            assert staged.read_bytes() == original
        finally:
            if nas.exists():
                nas.rmdir()
            if old_root.exists():
                old_root.rename(nas)

        saved = _persist(
            db_session,
            model,
            staged,
            is_external=True,
            external_library_id=library.id,
            dest_key_override=str(destination),
            ingestion_key="issue-210-root-replacement",
            blob_hash=hashlib.sha256(original).hexdigest(),
        )
        assert destination.read_bytes() == original
        assert saved.path == str(destination)
        assert (
            len(db_session.exec(select(File).where(File.model_id == model.id)).all())
            == 1
        )

    def test_persist_never_overwrites_an_unclaimed_destination(
        self, db_session: Session, storage, model: Model, tmp_path: Path
    ) -> None:
        occupied = Path(storage.blob_key(model.slug, 1, "bracket.stl"))
        occupied.parent.mkdir(parents=True, exist_ok=True)
        occupied.write_bytes(b"pre-existing user data")

        file_row = _persist(db_session, model, _staged(tmp_path))

        assert occupied.read_bytes() == b"pre-existing user data"
        assert file_row.path != str(occupied)
        assert Path(file_row.path).read_bytes() == b"solid bracket\nendsolid\n"

    def test_concurrent_same_hash_upload_dedups_instead_of_crashing(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Model.hash is UNIQUE. Two uploads of the same bytes race between the
        lookup and the insert; the loser must dedup onto the winner's model, not
        500 with an IntegrityError."""
        from app.db.session import get_session_factory
        from app.modules.storage import storage as storage_mod

        dedup_hash = "c" * 64
        real_ensure = storage_mod.ensure_unique_slug

        def _insert_the_winner(base_slug, exists):
            # Runs after resolve_or_create_model's SELECT found nothing and before
            # its INSERT lands — exactly the window the race lives in.
            with get_session_factory().session() as other:
                build_model(other, name="Winner", slug="winner", hash=dedup_hash)
            return real_ensure(base_slug, exists)

        monkeypatch.setattr(ingestion.storage, "ensure_unique_slug", _insert_the_winner)

        model, created = ingestion.resolve_or_create_model(
            db_session, dedup_hash=dedup_hash, model_name="Loser"
        )

        assert created is False
        assert model.name == "Winner"
        assert (
            len(db_session.exec(select(Model).where(Model.hash == dedup_hash)).all())
            == 1
        )


class TestReserveNextVersion:
    def test_version_numbers_increment_across_revisions(
        self, db_session: Session, storage, model: Model, tmp_path: Path
    ) -> None:
        first = _persist(db_session, model, _staged(tmp_path, "v1.stl"))
        second = _persist(db_session, model, _staged(tmp_path, "v2.stl"))

        assert (first.version, second.version) == (1, 2)

    def test_concurrent_version_reservations_are_unique(self, tmp_path: Path) -> None:
        engine = create_engine(
            f"sqlite:///{tmp_path / 'versions.sqlite'}",
            connect_args={"check_same_thread": False},
        )
        event.listen(engine, "connect", _set_sqlite_pragmas)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            concurrent_model = build_model(
                session, name="Concurrent", slug="concurrent", hash="c" * 64
            )
            model_id = concurrent_model.id
        assert model_id is not None

        start = threading.Barrier(3)
        versions: list[int] = []
        errors: list[BaseException] = []

        def reserve() -> None:
            try:
                with Session(engine) as session:
                    start.wait(timeout=5)
                    version = ingestion._reserve_next_version(session, model_id)
                    session.commit()
                    versions.append(version)
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        start.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=10)

        try:
            assert errors == []
            assert sorted(versions) == [1, 2]
            with Session(engine) as session:
                assert session.get(Model, model_id).next_file_version == 3
        finally:
            engine.dispose()

    def test_concurrent_artifacts_get_distinct_versions_under_contention(
        self, tmp_path: Path, storage
    ) -> None:
        engine = create_engine(
            f"sqlite:///{tmp_path / 'artifacts.sqlite'}",
            connect_args={"check_same_thread": False},
        )
        event.listen(engine, "connect", _set_sqlite_pragmas)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            concurrent_model = build_model(
                session, name="Race", slug="race", hash="a" * 64
            )
            model_id = concurrent_model.id
        assert model_id is not None

        staged: list[tuple[Path, bytes]] = []
        for index in range(2):
            content = f"G28 ; artifact {index}\n".encode()
            path = tmp_path / f"race-{index}.gcode"
            path.write_bytes(content)
            staged.append((path, content))

        start = threading.Barrier(3)
        errors: list[BaseException] = []
        session_factory = SQLiteSessionFactory(engine)

        def persist(path: Path, content: bytes) -> None:
            try:
                with Session(engine) as session:
                    model_row = session.get(Model, model_id)
                    assert model_row is not None
                    start.wait(timeout=5)
                    ingestion.persist_artifact(
                        session,
                        model=model_row,
                        staged_path=path,
                        original_filename=path.name,
                        file_type=FileType.GCODE,
                        blob_hash=hashlib.sha256(content).hexdigest(),
                        meta={},
                        session_factory=session_factory,
                    )
            except BaseException as exc:  # pragma: no cover - asserted below
                errors.append(exc)

        threads = [threading.Thread(target=persist, args=item) for item in staged]
        for thread in threads:
            thread.start()
        start.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=15)

        try:
            assert errors == []
            with Session(engine) as session:
                rows = session.exec(
                    select(File).where(File.model_id == model_id).order_by(File.version)
                ).all()
            assert [row.version for row in rows] == [1, 2]
            assert sum(row.is_recommended for row in rows) == 1
            for row in rows:
                assert (
                    hashlib.sha256(Path(row.path).read_bytes()).hexdigest()
                    == row.sha256
                )
        finally:
            engine.dispose()


class TestIncomingDimensionEvidence:
    @pytest.mark.parametrize("axis", ["bbox_x_mm", "bbox_y_mm", "bbox_z_mm"])
    @pytest.mark.parametrize("value", [float("inf"), float("-inf"), -1.0, True])
    def test_rejects_nonphysical_incoming_dimensions_before_publication(
        self, db_session, storage, model, tmp_path, monkeypatch, axis, value
    ):
        calls = []
        original_publish = ingestion.publish_file

        def observe_publication(*args, **kwargs):
            calls.append(kwargs)
            return original_publish(*args, **kwargs)

        monkeypatch.setattr(ingestion, "publish_file", observe_publication)
        staged = _staged(tmp_path)
        with pytest.raises(ValueError):
            _persist(db_session, model, staged, meta={axis: value})
        assert calls == []
        assert staged.exists()
        assert (
            db_session.exec(select(File).where(File.model_id == model.id)).all() == []
        )


class TestIncomingVolumeEvidence:
    @pytest.mark.parametrize(
        "meta",
        [
            {"volume_state": "measured"},
            {"volume_method": "mesh_surface_integral"},
            {"volume_unavailable_cause": "not_watertight"},
            {"volume_not_calculated_cause": "enrichment_pending"},
            {"volume_measurement": {}},
            {"volume_mm3": True},
            {"volume_mm3": float("inf")},
            {"volume_mm3": 10**400},
            {
                "volume_measurement": encode_volume(VolumeMeasured(1.0)),
                "volume_mm3": 2.0,
            },
        ],
        ids=[
            "internal-state",
            "internal-method",
            "internal-unavailable",
            "internal-not-calculated",
            "missing-wire-fields",
            "bool-scalar",
            "infinite-scalar",
            "overflow-scalar",
            "scalar-disagreement",
        ],
    )
    def test_rejects_invalid_volume_before_artifact_side_effects(
        self, db_session, storage, model, tmp_path, monkeypatch, meta
    ):
        publication_calls = []
        original_publish = ingestion.publish_file

        def observe_publication(*args, **kwargs):
            publication_calls.append(kwargs)
            return original_publish(*args, **kwargs)

        monkeypatch.setattr(ingestion, "publish_file", observe_publication)
        staged = _staged(tmp_path)
        next_version = model.next_file_version
        with pytest.raises(ValueError):
            _persist(db_session, model, staged, meta=meta)
        db_session.expire_all()
        assert publication_calls == []
        assert model.next_file_version == next_version
        assert staged.exists()
        assert (
            db_session.exec(select(File).where(File.model_id == model.id)).all() == []
        )
        assert list(storage.walk_keys()) == []

    @pytest.mark.parametrize(
        "volume",
        [
            VolumeMeasured(1e-9),
            VolumeUnavailable(VolumeUnavailableCause.NOT_WATERTIGHT),
            VolumeNotCalculated(VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED),
        ],
    )
    def test_persists_explicit_volume_evidence(
        self, db_session, storage, model, tmp_path, volume
    ):
        from app.modules.library.volume_metadata import read_volume

        artifact = _persist(
            db_session,
            model,
            _staged(tmp_path),
            meta={"volume_measurement": encode_volume(volume)},
        )
        metadata = db_session.exec(
            select(Metadata).where(Metadata.file_id == artifact.id)
        ).one()
        assert read_volume(metadata) == volume

    @pytest.mark.parametrize("value", [None, 0.0, -1.0, 1e-9, 500.0])
    def test_preserves_scalar_only_payload_as_unassessed(
        self, db_session, storage, model, tmp_path, value
    ):
        from app.modules.library.volume_metadata import read_volume

        artifact = _persist(
            db_session, model, _staged(tmp_path), meta={"volume_mm3": value}
        )
        metadata = db_session.exec(
            select(Metadata).where(Metadata.file_id == artifact.id)
        ).one()
        assert read_volume(metadata) == VolumeLegacyUnassessed(value)
        assert metadata.volume_mm3 == value


class TestMetadata:
    def test_unknown_commit_resolution_keeps_the_published_blob(
        self,
        db_session: Session,
        storage,
        model: Model,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        original_commit = db_session.commit
        commit_calls = 0

        def commit_then_lose_ack() -> None:
            nonlocal commit_calls
            commit_calls += 1
            original_commit()
            if commit_calls == 2:
                raise ConnectionError("acknowledgement lost after commit")

        monkeypatch.setattr(db_session, "commit", commit_then_lose_ack)
        monkeypatch.setattr(
            ingestion,
            "_resolve_committed_artifact",
            lambda **_kwargs: (_ for _ in ()).throw(OSError("database unavailable")),
        )

        with pytest.raises(ingestion.ArtifactCommitUncertain):
            _persist(db_session, model, _staged(tmp_path))

        # An unresolved acknowledgement is not permission to roll back storage.
        db_session.rollback()
        assert list(storage.walk_keys())

    def test_commit_ack_loss_resolves_from_a_fresh_session(
        self,
        db_session: Session,
        storage,
        model: Model,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A successful COMMIT followed by lost acknowledgement is terminal."""
        original_commit = db_session.commit
        commit_calls = 0

        def commit_then_lose_ack() -> None:
            nonlocal commit_calls
            commit_calls += 1
            original_commit()
            # The first commit closes the caller's read transaction. The
            # second is the File+Metadata ownership boundary.
            if commit_calls == 2:
                raise ConnectionError("acknowledgement lost after commit")

        monkeypatch.setattr(db_session, "commit", commit_then_lose_ack)
        file_row = _persist(db_session, model, _staged(tmp_path))

        assert file_row.id is not None
        with get_session_factory().session() as fresh:
            durable = fresh.get(File, file_row.id)
            assert durable is not None
            assert (
                fresh.exec(
                    select(Metadata).where(Metadata.file_id == file_row.id)
                ).first()
                is not None
            )
            assert durable.thumbnail_path is None
        assert Path(file_row.path).exists()
        assert not Path(storage.thumbnail_key(file_row.id)).exists()

    def test_persists_a_file_row_with_its_metadata_in_one_commit(
        self, db_session: Session, storage, model: Model, tmp_path: Path
    ) -> None:
        file_row = _persist(db_session, model, _staged(tmp_path))

        assert file_row.id is not None
        md = db_session.exec(
            select(Metadata).where(Metadata.file_id == file_row.id)
        ).first()
        assert md is not None and md.estimated_time_s == 120

    def test_rolls_back_precommit_bytes_on_cooperative_cancellation(
        self, db_session, storage, model, tmp_path, monkeypatch
    ):
        from app.core.cancellation import OperationCancelled

        cancellation = OperationCancelled()
        published = []
        metadata_file_ids = []
        original_publish = ingestion.publish_file

        def observe_publication(*args, **kwargs):
            receipt = original_publish(*args, **kwargs)
            published.append(receipt)
            assert Path(receipt.key).read_bytes() == b"solid bracket\nendsolid\n"
            return receipt

        monkeypatch.setattr(ingestion, "publish_file", observe_publication)

        def withdrawn_metadata(*_args, **kwargs):
            metadata_file_ids.append(kwargs["file_id"])
            raise cancellation

        withdrawn_metadata.model_fields = ingestion.Metadata.model_fields
        monkeypatch.setattr(ingestion, "Metadata", withdrawn_metadata)

        with pytest.raises(OperationCancelled) as raised:
            _persist(db_session, model, _staged(tmp_path))

        db_session.rollback()
        assert raised.value is cancellation
        assert (
            db_session.exec(select(File).where(File.model_id == model.id)).all() == []
        )
        assert len(metadata_file_ids) == 1
        assert db_session.exec(
            select(Metadata).where(Metadata.file_id == metadata_file_ids[0])
        ).all() == []
        assert len(published) == 1
        assert not Path(published[0].key).exists()

    def test_preserves_cancellation_when_receipt_cleanup_fails(
        self, db_session, storage, model, tmp_path, monkeypatch
    ):
        from app.core.cancellation import OperationCancelled

        cancellation = OperationCancelled()
        receipts = []

        def withdrawn_metadata(*_args, **_kwargs):
            raise cancellation

        def failed_cleanup(_backend, receipt):
            receipts.append(receipt)
            raise OSError("receipt cleanup unavailable")

        withdrawn_metadata.model_fields = ingestion.Metadata.model_fields
        monkeypatch.setattr(ingestion, "Metadata", withdrawn_metadata)
        monkeypatch.setattr(LocalStorageBackend, "rollback_create", failed_cleanup)

        with pytest.raises(OperationCancelled) as raised:
            _persist(db_session, model, _staged(tmp_path))

        assert raised.value is cancellation
        assert any("receipt cleanup" in note for note in cancellation.__notes__)
        assert (
            db_session.exec(select(File).where(File.model_id == model.id)).all() == []
        )
        assert len(receipts) == 1
        assert Path(receipts[0].key).read_bytes() == b"solid bracket\nendsolid\n"

    def test_preserves_committed_artifact_after_cooperative_cancellation(
        self, db_session, storage, model, tmp_path, monkeypatch
    ):
        from app.core.cancellation import OperationCancelled

        cancellation = OperationCancelled()
        original_commit = db_session.commit
        commit_calls = 0
        model_id = model.id
        staged = _staged(tmp_path)
        original_bytes = staged.read_bytes()

        def commit_then_cancel():
            nonlocal commit_calls
            commit_calls += 1
            original_commit()
            if commit_calls == 2:
                raise cancellation

        monkeypatch.setattr(db_session, "commit", commit_then_cancel)
        with pytest.raises(OperationCancelled) as raised:
            _persist(db_session, model, staged)

        assert raised.value is cancellation
        with get_session_factory().session() as fresh:
            durable = fresh.exec(select(File).where(File.model_id == model_id)).one()
            assert Path(durable.path).read_bytes() == original_bytes
            metadata = fresh.exec(
                select(Metadata).where(Metadata.file_id == durable.id)
            ).one()
            assert metadata.estimated_time_s == 120

    def test_failed_metadata_does_not_leave_orphan_file_row(
        self,
        db_session: Session,
        storage,
        model: Model,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A File row without its Metadata is the silent-corruption case."""

        def _boom(*_args, **_kwargs):
            raise RuntimeError("metadata boom")

        # ``Metadata`` is only ever called to construct the row; model_fields is read
        # first, so keep that attribute intact.
        _boom.model_fields = ingestion.Metadata.model_fields
        monkeypatch.setattr(ingestion, "Metadata", _boom)

        with pytest.raises(RuntimeError, match="metadata boom"):
            _persist(db_session, model, _staged(tmp_path))

        db_session.rollback()
        assert (
            db_session.exec(select(File).where(File.model_id == model.id)).all() == []
        )
        assert not Path(storage.blob_key(model.slug, 1, "bracket.stl")).exists()


class TestProvenance:
    def test_provenance_attachment_shares_artifact_transaction(
        self,
        db_session: Session,
        storage,
        model: Model,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A provenance failure must roll back the freshly-flushed Artifact too."""

        seen_file_ids: list[int] = []

        def _boom(session: Session, file_row: File, context: object) -> None:
            del session, context
            assert file_row.id is not None
            seen_file_ids.append(file_row.id)
            raise RuntimeError("provenance boom")

        monkeypatch.setattr(ingestion, "_attach_ingested_artifact", _boom)

        with pytest.raises(RuntimeError, match="provenance boom"):
            _persist(
                db_session,
                model,
                _staged(tmp_path),
                provenance_context=object(),
            )

        db_session.rollback()
        assert seen_file_ids
        assert (
            db_session.exec(select(File).where(File.model_id == model.id)).all() == []
        )

    def test_deduplicated_pipeline_recapture_refreshes_provenance_without_new_artifact(
        self, db_session: Session, storage, tmp_path: Path
    ) -> None:
        """A reusable blob still records a newer source snapshot before terminal dedupe."""
        staged = _staged(tmp_path)
        blob_hash = hashlib.sha256(staged.read_bytes()).hexdigest()
        actor = build_user(db_session, "capture-owner", superuser=True)
        model = build_model(db_session, name="Bracket", slug="bracket", hash=blob_hash)
        db_session.refresh(actor)
        db_session.refresh(model)
        assert model.id is not None
        file_row = build_file(
            db_session,
            model,
            path="provenance/existing.stl",
            filename=staged.name,
            file_type=FileType.STL,
            size_bytes=staged.stat().st_size,
            sha256=blob_hash,
        )

        def manifest(title: str, revision: str):
            return provenance.CaptureManifestV2.from_dict(
                {
                    "schema_version": 2,
                    "kind": "model_files",
                    "source": {
                        "provider": "printables",
                        "canonical_url": "https://printables.com/model/42",
                        "source_item_id": "42",
                        "source_revision": revision,
                        "adapter_version": "test",
                        "tags": [],
                        "fields": {"title": {"value": title, "origin": "confirmed"}},
                    },
                    "files": [
                        {
                            "id": "42:file",
                            "name": staged.name,
                            "file_type": "stl",
                            "size": staged.stat().st_size,
                        }
                    ],
                    "selected_ids": ["42:file"],
                }
            )

        first = provenance.ProvenanceContext(
            manifest=manifest("Original", "r1"),
            source_file_id="42:file",
            source_filename=staged.name,
            blob_sha256=blob_hash,
            actor_id=actor.id,
        )
        link = provenance.attach_ingested_artifact(db_session, file_row, first)
        provenance.set_user_override(
            db_session,
            provenance_source_id=link.provenance_source_id,
            field_name="title",
            value="Local",
        )
        db_session.commit()
        engine = db_session.get_bind()
        assert isinstance(engine, Engine)
        ingestion.commit_staged_artifact(
            ingestion.StagedArtifact(
                staged_path=staged,
                original_filename=staged.name,
                model_name="Ignored",
                file_type=FileType.STL,
            ),
            ingestion_key="recapture",
            actor_user_id=actor.id,
            session_factory=SQLiteSessionFactory(engine),
            provenance_context=provenance.ProvenanceContext(
                manifest=manifest("Changed", "r2"),
                source_file_id="42:file",
                source_filename=staged.name,
                actor_id=actor.id,
            ),
        )
        assert db_session.exec(select(File).where(File.model_id == model.id)).all() == [
            file_row
        ]
        assert len(db_session.exec(select(ProvenanceCapture)).all()) == 2
        title = db_session.exec(
            select(ModelProvenanceField).where(
                ModelProvenanceField.provenance_source_id == link.provenance_source_id
            )
        ).one()
        assert provenance.effective_value(title) == "Local"


class TestDerivativeHandoff:
    def test_commits_the_artifact_without_a_thumbnail(
        self, db_session: Session, storage, model: Model, tmp_path: Path
    ) -> None:
        file_row = _persist(db_session, model, _staged(tmp_path))

        assert file_row.thumbnail_path is None

    def test_nudges_the_derivative_source_after_the_commit(
        self, db_session: Session, storage, model: Model, tmp_path: Path
    ) -> None:
        _persist(db_session, model, _staged(tmp_path))

        cursor = db_session.get(ReconcileCursor, JobKind.DERIVATIVES_MESH)
        assert cursor is not None and cursor.nudged_at is not None

    def test_never_nudges_for_an_artifact_that_did_not_commit(
        self,
        db_session: Session,
        storage,
        model: Model,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _boom(*_args, **_kwargs):
            raise RuntimeError("metadata boom")

        _boom.model_fields = ingestion.Metadata.model_fields
        monkeypatch.setattr(ingestion, "Metadata", _boom)

        with pytest.raises(RuntimeError, match="metadata boom"):
            _persist(db_session, model, _staged(tmp_path))

        db_session.rollback()
        assert db_session.get(ReconcileCursor, JobKind.DERIVATIVES_MESH) is None


class TestStagedAttemptPublication:
    def test_late_upload_completion_preserves_retried_staging(
        self, db_session, make_user, make_ingest_request, tmp_path, storage, monkeypatch
    ):
        from app.db.models import IngestRequestKind, Job, JobState, StagingLease
        from app.modules.ingestion import staging_leases
        from app.modules.work import service
        from tests.factories.ops import build_job_context

        owner = make_user()
        request = make_ingest_request(owner, kind=IngestRequestKind.UPLOAD)
        staged = tmp_path / "attempt.gcode"
        original = b"; original source\nG1 X1 Y1\n"
        replacement = b"; retry-owned bytes\nG1 X2 Y2\n"
        staged.write_bytes(original)
        staging_leases.create_job_lease(
            db_session,
            job_id=request.job_id,
            owner_user_id=owner.id,
            path=staged,
            size_bytes=len(original),
            sha256=hashlib.sha256(original).hexdigest(),
        )
        db_session.commit()
        context = build_job_context(request.job_id)
        monkeypatch.setattr(service, "nudge", lambda *_args, **_kwargs: None)
        retried_leases = []

        def retry_before_terminal(stage, job_id):
            if stage != "before_terminal":
                return
            service.cancel(job_id, actor=owner)
            staged.write_bytes(replacement)
            lease = staging_leases.create_job_lease(
                db_session,
                job_id=job_id,
                owner_user_id=owner.id,
                path=staged,
                size_bytes=len(replacement),
                sha256=hashlib.sha256(replacement).hexdigest(),
            )
            db_session.commit()
            retried_leases.append(lease.id)
            service.retry(job_id, actor=owner)

        monkeypatch.setattr(
            ingestion, "_fault_injection_checkpoint", retry_before_terminal
        )

        outcome = ingestion.ingest_staged_file(
            job_context=context,
            artifact=ingestion.StagedArtifact(
                staged_path=staged,
                original_filename="attempt.gcode",
                model_name="Attempt",
                file_type=FileType.GCODE,
            ),
            actor_user_id=owner.id,
        )

        assert outcome is not None
        db_session.expire_all()
        job = db_session.get(Job, request.job_id)
        assert job.state is JobState.QUEUED
        assert job.attempts == context.attempt
        assert job.execution_epoch != context.execution_epoch
        assert staged.read_bytes() == replacement
        assert db_session.get(StagingLease, retried_leases[0]) is not None
        assert (
            db_session.get(File, outcome.file_id).sha256
            == hashlib.sha256(original).hexdigest()
        )
