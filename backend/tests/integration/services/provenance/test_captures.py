"""Keeping "where did this model come from" true across recaptures and merges.

Provenance is the record a user relies on for attribution and licensing, so it
has to survive being captured twice, captured from two URLs that turn out to be
the same page, and moved between instances. The properties this file defends are
all about *identity* rather than content:

**The snapshot hash is canonical.** The same provider page captured twice must
produce the same hash, or every recapture looks like a change and the history
becomes noise. That means key order and formatting cannot leak into it.

**Capture is idempotent, and an explicit override wins.** A user who corrected the
creator name must not have that correction overwritten by the next recapture —
including when their override is deliberately *empty*, which is a statement
("the source is wrong, there is no creator") rather than a missing value.

**Merging two sources cannot lose a cover.** A legacy URL-keyed source promoted
to a stable id can collide with an existing one. Whichever way the merge goes, the
obsolete cover's bytes are either transferred or their exact receipt is enqueued
for deletion — never dropped on the floor, since bytes nothing owns are bytes
nothing will ever clean up.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, select

from app.db.models import (
    ArtifactProvenanceLink,
    File,
    FileType,
    Model,
    ModelProvenanceField,
    ModelProvenanceSource,
    ModelSourceCover,
    ProvenanceCapture,
    StorageDeleteIntent,
)
from app.schemas.provenance import CaptureManifestV2
from app.services import provenance, storage_deletion, trash
from app.services.storage_backend import StorageBackend, get_backend
from app.services.storage_ownership import (
    UnsafeStorageDeleteError,
    record_creation,
)
from tests.factories import (
    build_file,
    build_model,
    build_user,
)


def _model(session: Session) -> Model:
    row = build_model(session, name="Bracket", slug="bracket", hash="a" * 64)
    # The shared SQLite fixture predates provenance tables; clear any rows
    # whose reused in-memory model id survived its legacy teardown list.
    # The legacy fixture's teardown did not know the new dependent tables.
    # Clear them explicitly in this module so reused SQLite ids cannot make a
    # capture history from a prior test look like this model's history.
    for link in session.exec(select(ArtifactProvenanceLink)).all():
        session.delete(link)
    for capture in session.exec(select(ProvenanceCapture)).all():
        session.delete(capture)
    for field in session.exec(select(ModelProvenanceField)).all():
        session.delete(field)
    for source in session.exec(
        select(ModelProvenanceSource).where(ModelProvenanceSource.model_id == row.id)
    ).all():
        session.delete(source)
    session.flush()
    return row


def _capture(*, title: str = "Bracket") -> CaptureManifestV2:
    return CaptureManifestV2.from_dict(
        {
            "schema_version": 2,
            "kind": "model_files",
            "source": {
                "provider": "printables",
                "canonical_url": "https://Printables.com/model/42?utm_source=test#details",
                "source_item_id": "42",
                "source_revision": None,
                "adapter_version": "printables-v1",
                "fields": {"title": {"value": title, "origin": "confirmed"}},
            },
            "files": [
                {
                    "id": "42:file-a",
                    "name": "part.stl",
                    "file_type": "stl",
                    "size": None,
                }
            ],
            "selected_ids": ["42:file-a"],
        }
    )


def _legacy_capture(*, title: str = "Bracket") -> CaptureManifestV2:
    """The pre-V2 URL identity, as stored before a stable item id arrived."""
    data = _capture(title=title).to_dict()
    data["source"]["source_item_id"] = None
    return CaptureManifestV2.from_dict(data)


def _capture_without_title() -> CaptureManifestV2:
    data = _capture().to_dict()
    data["source"]["fields"].pop("title")
    return CaptureManifestV2.from_dict(data)


class TestSnapshotIdentity:
    """What makes two captures of the same page the same capture.

    `canonicalize_url`'s own rules live next door in `test_values.py`; what these
    assert is that a whole manifest reduces through it to one stable identity.
    """

    def test_canonicalizes_a_source_url(self) -> None:
        capture = _capture()

        canonical = provenance.canonicalize_url(capture.source.canonical_url)

        assert canonical == "https://printables.com/model/42"

    def test_hashes_two_identical_captures_the_same(self) -> None:
        assert provenance.snapshot_sha256(_capture()) == provenance.snapshot_sha256(
            _capture()
        )

    def test_derives_one_identity_key_for_identical_captures(self) -> None:
        assert provenance.identity_key(_capture()) == provenance.identity_key(
            _capture()
        )


class TestUpsertCapture:
    def test_replaces_the_capture_row_only_when_the_manifest_changes(
        self, db_session: Session
    ) -> None:
        model = _model(db_session)
        initial = provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_capture()
        )
        db_session.commit()
        duplicate = provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_capture()
        )
        provenance.set_user_override(
            db_session,
            provenance_source_id=initial.source.id,
            field_name="title",
            value="",
        )
        changed = provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_capture(title="Changed")
        )
        db_session.commit()

        assert duplicate.capture.id == initial.capture.id
        assert changed.capture.id != initial.capture.id
        title = db_session.exec(
            select(ModelProvenanceField).where(
                ModelProvenanceField.provenance_source_id == initial.source.id,
                ModelProvenanceField.field_name == "title",
            )
        ).one()
        assert provenance.effective_value(title) == ""
        assert title.user_override_set is True
        assert json.loads(title.captured_value_json) == "Changed"
        assert (
            len(
                db_session.exec(
                    select(ProvenanceCapture).where(
                        ProvenanceCapture.provenance_source_id == initial.source.id
                    )
                ).all()
            )
            == 2
        )

    def test_promotes_a_legacy_url_source_onto_its_stable_id(
        self,
        db_session: Session,
    ) -> None:
        model = _model(db_session)
        legacy = provenance.upsert_capture(
            db_session,
            model_id=model.id,
            manifest=_legacy_capture(title="Legacy title"),
        )
        provenance.set_user_override(
            db_session,
            provenance_source_id=legacy.source.id,
            field_name="title",
            value="Local title",
        )
        db_session.commit()

        stable = provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_capture(title="Remote title")
        )
        db_session.commit()

        sources = db_session.exec(
            select(ModelProvenanceSource).where(
                ModelProvenanceSource.model_id == model.id
            )
        ).all()
        assert len(sources) == 1
        assert stable.source.id == legacy.source.id
        assert stable.source.source_item_id == "42"
        title = db_session.exec(
            select(ModelProvenanceField).where(
                ModelProvenanceField.provenance_source_id == stable.source.id,
                ModelProvenanceField.field_name == "title",
            )
        ).one()
        assert provenance.effective_value(title) == "Local title"
        assert (
            len(
                db_session.exec(
                    select(ProvenanceCapture).where(
                        ProvenanceCapture.provenance_source_id == stable.source.id
                    )
                ).all()
            )
            == 2
        )

    def test_source_merge_transfers_obsolete_cover_when_target_has_none(
        self,
        db_session: Session,
    ) -> None:
        model = _model(db_session)
        stable = provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_capture(title="Stable")
        )
        legacy = provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_legacy_capture(title="Legacy")
        )
        db_session.commit()
        assert stable.source.id is not None and legacy.source.id is not None

        backend = get_backend()
        key = backend.source_cover_key(legacy.source.id)
        receipt = backend.create_bytes(b"legacy-cover", key)
        obsolete_cover = ModelSourceCover(
            provenance_source_id=legacy.source.id,
            storage_key=key,
            size_bytes=receipt.size,
        )
        db_session.add(obsolete_cover)
        record_creation(db_session, receipt, object_kind="model_source_cover")
        db_session.commit()

        merged = provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_capture(title="Stable update")
        )
        db_session.commit()

        cover = db_session.exec(select(ModelSourceCover)).one()
        assert cover.id == obsolete_cover.id
        assert cover.provenance_source_id == merged.source.id
        assert db_session.exec(select(StorageDeleteIntent)).all() == []
        assert backend.exists(key)

    def test_source_merge_enqueues_exact_obsolete_cover_receipt_when_target_has_cover(
        self,
        db_session: Session,
    ) -> None:
        model = _model(db_session)
        stable = provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_capture(title="Stable")
        )
        legacy = provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_legacy_capture(title="Legacy")
        )
        db_session.commit()
        assert stable.source.id is not None and legacy.source.id is not None

        backend = get_backend()
        target_key = backend.source_cover_key(stable.source.id)
        obsolete_key = backend.source_cover_key(legacy.source.id)
        target_receipt = backend.create_bytes(b"stable-cover", target_key)
        obsolete_receipt = backend.create_bytes(b"legacy-cover", obsolete_key)
        target_cover = ModelSourceCover(
            provenance_source_id=stable.source.id,
            storage_key=target_key,
            size_bytes=target_receipt.size,
        )
        obsolete_cover = ModelSourceCover(
            provenance_source_id=legacy.source.id,
            storage_key=obsolete_key,
            size_bytes=obsolete_receipt.size,
        )
        db_session.add_all([target_cover, obsolete_cover])
        record_creation(db_session, target_receipt, object_kind="model_source_cover")
        record_creation(db_session, obsolete_receipt, object_kind="model_source_cover")
        db_session.commit()

        provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_capture(title="Stable update")
        )
        db_session.commit()

        covers = db_session.exec(select(ModelSourceCover)).all()
        assert len(covers) == 1
        assert covers[0].storage_key == target_key
        intents = db_session.exec(
            select(StorageDeleteIntent).where(
                StorageDeleteIntent.resource_kind == "model_source_cover"
            )
        ).all()
        assert len(intents) == 1
        assert intents[0].key == obsolete_receipt.key
        assert intents[0].token == obsolete_receipt.token
        assert intents[0].resource_id == str(obsolete_cover.id)
        assert backend.exists(obsolete_key)

        assert storage_deletion.process_storage_delete_intents().completed == 1
        assert not backend.exists(obsolete_key)

    def test_provenance_follows_its_models_whole_lifecycle(
        self,
        db_session: Session,
    ) -> None:
        """Provenance follows its Model's lifecycle but never owns Artifact bytes."""
        model = _model(db_session)
        assert model.id is not None
        actor = build_user(db_session, "provenance-admin", superuser=True)
        artifact = build_file(
            db_session,
            model,
            path="external/provenance-part.stl",
            filename="part.stl",
            file_type=FileType.STL,
            size_bytes=1,
            sha256="d" * 64,
            external=True,
        )
        db_session.add_all([actor, artifact])
        db_session.commit()
        db_session.refresh(actor)
        db_session.refresh(artifact)
        assert actor.id is not None and artifact.id is not None

        context = provenance.ProvenanceContext(
            manifest=_capture(),
            source_file_id="42:file-a",
            source_filename="part.stl",
            blob_sha256="d" * 64,
            actor_id=actor.id,
        )
        link = provenance.attach_ingested_artifact(db_session, artifact, context)
        db_session.commit()
        source_id = link.provenance_source_id
        capture_id = link.capture_id

        assert (
            provenance.preflight_existing_artifact(db_session, context).status
            == "reusable"
        )

        trash.soft_delete_model(db_session, model)
        trashed = provenance.preflight_existing_artifact(db_session, context)
        assert trashed.status == "trashed"
        assert trashed.link is trashed.file_id is None
        assert trashed.model_id == model.id

        trash.restore_model(db_session, model)
        reused = provenance.preflight_existing_artifact(db_session, context)
        assert reused.status == "reusable"
        assert reused.link is not None and reused.link.id == link.id
        assert reused.model_id == model.id and reused.file_id == artifact.id
        assert db_session.get(ModelProvenanceSource, source_id) is not None

        # A capture is history, not an Artifact owner: deleting one keeps its link
        # but clears the optional snapshot reference according to the FK contract.
        # The shared SQLite teardown temporarily disables FK enforcement while
        # wiping tables; restore production-equivalent enforcement for this
        # database-level lifecycle contract.
        db_session.commit()
        capture = db_session.get(ProvenanceCapture, capture_id)
        assert capture is not None
        db_session.delete(capture)
        db_session.commit()
        db_session.expire_all()
        assert db_session.get(ProvenanceCapture, capture_id) is None
        persisted_link = db_session.get(ArtifactProvenanceLink, link.id)
        assert persisted_link is not None and persisted_link.capture_id is None

        trash.hard_delete_model(db_session, model)
        db_session.commit()

        assert db_session.get(Model, model.id) is None
        assert db_session.get(File, artifact.id) is None
        assert db_session.get(ModelProvenanceSource, source_id) is None
        assert db_session.exec(select(ModelProvenanceField)).all() == []
        assert db_session.exec(select(ProvenanceCapture)).all() == []
        assert db_session.exec(select(ArtifactProvenanceLink)).all() == []
        assert {
            intent.resource_kind
            for intent in db_session.exec(select(StorageDeleteIntent)).all()
        } <= {"file_thumbnail", "file_thumbnail_legacy"}

    def test_source_merge_cover_delete_proof_failure_rolls_back_the_whole_merge(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        model = _model(db_session)
        stable = provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_capture(title="Stable")
        )
        legacy = provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_legacy_capture(title="Legacy")
        )
        db_session.commit()
        assert stable.source.id is not None and legacy.source.id is not None

        backend = get_backend()
        target_key = backend.source_cover_key(stable.source.id)
        obsolete_key = backend.source_cover_key(legacy.source.id)
        target_receipt = backend.create_bytes(b"stable-cover", target_key)
        obsolete_receipt = backend.create_bytes(b"legacy-cover", obsolete_key)
        target_cover = ModelSourceCover(
            provenance_source_id=stable.source.id,
            storage_key=target_key,
            size_bytes=target_receipt.size,
        )
        obsolete_cover = ModelSourceCover(
            provenance_source_id=legacy.source.id,
            storage_key=obsolete_key,
            size_bytes=obsolete_receipt.size,
        )
        db_session.add_all([target_cover, obsolete_cover])
        record_creation(db_session, target_receipt, object_kind="model_source_cover")
        record_creation(db_session, obsolete_receipt, object_kind="model_source_cover")
        db_session.commit()

        unverified = MagicMock(spec=StorageBackend)
        unverified.creation_matches.return_value = False
        monkeypatch.setattr(provenance, "get_backend", lambda: unverified)

        with pytest.raises(
            UnsafeStorageDeleteError, match="storage_ownership_unverified"
        ):
            provenance.upsert_capture(
                db_session, model_id=model.id, manifest=_capture(title="Stable update")
            )
        db_session.rollback()

        sources = db_session.exec(
            select(ModelProvenanceSource).where(
                ModelProvenanceSource.model_id == model.id
            )
        ).all()
        assert {source.id for source in sources} == {stable.source.id, legacy.source.id}
        assert {
            cover.provenance_source_id
            for cover in db_session.exec(select(ModelSourceCover))
        } == {stable.source.id, legacy.source.id}
        assert db_session.exec(select(StorageDeleteIntent)).all() == []
        assert backend.exists(target_key)
        assert backend.exists(obsolete_key)


class TestAttachExistingArtifact:
    def test_portable_attach_leaves_an_existing_capture_alone(
        self,
        db_session: Session,
    ) -> None:
        model = _model(db_session)
        artifact = build_file(
            db_session,
            model,
            path="provenance/existing.stl",
            filename="existing.stl",
            file_type=FileType.STL,
            size_bytes=1,
            sha256="c" * 64,
        )
        original = provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_capture(title="Original")
        )
        provenance.set_user_override(
            db_session,
            provenance_source_id=original.source.id,
            field_name="title",
            value="Local title",
        )
        db_session.commit()

        context = provenance.ProvenanceContext(
            manifest=_capture(title="Incoming"),
            source_file_id="42:file-a",
            source_filename="part.stl",
            blob_sha256="c" * 64,
        )
        result = provenance.attach_existing_artifact(
            db_session,
            artifact,
            context,
            imported_overrides={"title": "Imported title"},
        )
        db_session.commit()

        title = db_session.exec(
            select(ModelProvenanceField).where(
                ModelProvenanceField.provenance_source_id == original.source.id,
                ModelProvenanceField.field_name == "title",
            )
        ).one()
        assert json.loads(title.captured_value_json) == "Original"
        assert provenance.effective_value(title) == "Local title"
        assert result.imported_override_fields == ()
        assert result.conflicting_override_fields == ("title",)
        assert (
            len(
                db_session.exec(
                    select(ProvenanceCapture).where(
                        ProvenanceCapture.provenance_source_id == original.source.id
                    )
                ).all()
            )
            == 2
        )


class TestIdentityKey:
    def test_preflight_does_not_disclose_a_link_to_an_unrelated_actor(
        self,
        db_session: Session,
    ) -> None:
        model = _model(db_session)
        artifact = build_file(
            db_session,
            model,
            path="provenance/part.stl",
            filename="part.stl",
            file_type=FileType.STL,
            size_bytes=1,
            sha256="b" * 64,
        )
        source = ModelProvenanceSource(
            model_id=model.id,
            provider="printables",
            canonical_url="https://printables.com/model/42",
            identity_key=provenance.identity_key(_capture()),
        )
        db_session.add_all([artifact, source])
        db_session.flush()
        db_session.add(
            ArtifactProvenanceLink(
                file_id=artifact.id,
                provenance_source_id=source.id,
                source_filename="part.stl",
                blob_sha256="b" * 64,
                import_key=provenance.import_key(
                    _capture(),
                    source_file_id=None,
                    source_filename="part.stl",
                    blob_sha256="b" * 64,
                ),
            )
        )
        actor = build_user(db_session, "unrelated")
        db_session.add(actor)
        db_session.commit()

        result = provenance.preflight_existing_artifact(
            db_session,
            provenance.ProvenanceContext(
                manifest=_capture(),
                source_file_id=None,
                source_filename="part.stl",
                blob_sha256="b" * 64,
                actor_id=actor.id,
            ),
        )
        assert result.status == "not_found"
        assert result.link is result.model_id is result.file_id is None


def _import_key(capture, *, filename: str = "part.stl", blob: str = "b" * 64) -> str:
    """`import_key` for one capture, varying only the field a test is about."""
    return provenance.import_key(
        capture,
        source_file_id="file-a",
        source_filename=filename,
        blob_sha256=blob,
    )


class TestImportKey:
    def test_import_key_ignores_a_renamed_source_file(self) -> None:
        capture = _capture()

        assert _import_key(capture, filename="part.stl") == _import_key(
            capture, filename="renamed.stl"
        )

    def test_import_key_changes_with_the_blob_bytes(self) -> None:
        capture = _capture()

        assert _import_key(capture, blob="b" * 64) != _import_key(
            capture, blob="c" * 64
        )


class TestClearUserOverride:
    def test_clear_user_override_is_idempotent_for_absent_capture_field(
        self,
        db_session: Session,
    ) -> None:
        model = _model(db_session)
        captured = provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_capture_without_title()
        )
        row = provenance.set_user_override(
            db_session,
            provenance_source_id=captured.source.id,
            field_name="title",
            value="Local title",
        )
        db_session.commit()
        user_updated_at = row.user_updated_at

        first = provenance.clear_user_override(
            db_session,
            provenance_source_id=captured.source.id,
            field_name="title",
        )
        second = provenance.clear_user_override(
            db_session,
            provenance_source_id=captured.source.id,
            field_name="title",
        )
        db_session.commit()

        assert first is row
        assert second is row
        assert row.user_override_set is False
        assert row.user_value_json is None
        assert row.captured_value_json == '""'
        assert provenance.effective_value(row) == ""
        assert (
            row.user_updated_at is not None and row.user_updated_at >= user_updated_at
        )

    def test_clear_user_override_is_noop_when_field_row_is_absent(
        self,
        db_session: Session,
    ) -> None:
        model = _model(db_session)
        captured = provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_capture_without_title()
        )

        first = provenance.clear_user_override(
            db_session,
            provenance_source_id=captured.source.id,
            field_name="title",
        )
        second = provenance.clear_user_override(
            db_session,
            provenance_source_id=captured.source.id,
            field_name="title",
        )
        db_session.commit()

        assert first is None
        assert second is None
        assert (
            db_session.exec(
                select(ModelProvenanceField).where(
                    ModelProvenanceField.provenance_source_id == captured.source.id,
                    ModelProvenanceField.field_name == "title",
                )
            ).all()
            == []
        )


class TestSetUserOverride:
    def test_user_override_creates_field_when_capture_omits_allowlisted_field(
        self,
        db_session: Session,
    ) -> None:
        model = _model(db_session)
        actor = build_user(db_session, "provenance-override-owner")
        db_session.add(actor)
        db_session.flush()
        captured = provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_capture_without_title()
        )

        row = provenance.set_user_override(
            db_session,
            provenance_source_id=captured.source.id,
            field_name="title",
            value="Local title",
            actor_id=actor.id,
        )
        db_session.commit()

        assert row.id is not None
        assert row.captured_value_json == '""'
        assert row.captured_origin == "inferred"
        assert row.captured_at is None
        assert row.user_override_set is True
        assert row.user_updated_by == actor.id
        assert row.user_updated_at is not None
        assert provenance.effective_value(row) == "Local title"
        assert json.loads(row.user_value_json or "null") == "Local title"
        assert (
            len(
                db_session.exec(
                    select(ProvenanceCapture).where(
                        ProvenanceCapture.provenance_source_id == captured.source.id
                    )
                ).all()
            )
            == 1
        )
