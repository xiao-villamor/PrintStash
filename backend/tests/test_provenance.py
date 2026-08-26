from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from sqlmodel import Session, select

from app.db.models import (
    ArtifactProvenanceLink,
    File,
    FileType,
    InboxItem,
    InboxItemResult,
    InboxItemResultState,
    Model,
    ModelProvenanceField,
    ModelProvenanceSource,
    ModelSourceCover,
    ProvenanceCapture,
    StorageDeleteIntent,
    User,
)
from app.schemas.provenance import CaptureManifestV2
from app.services import provenance, storage_deletion, trash
from app.services.storage_backend import StorageBackend, get_backend
from app.services.storage_ownership import (
    UnsafeStorageDeleteError,
    record_creation,
)


def _model(session: Session) -> Model:
    row = Model(name="Bracket", slug="bracket", hash="a" * 64)
    session.add(row)
    session.commit()
    session.refresh(row)
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


def test_snapshot_hash_is_canonical_and_identity_uses_stable_source_id() -> None:
    first = _capture()
    second = _capture()

    assert (
        provenance.canonicalize_url(first.source.canonical_url)
        == "https://printables.com/model/42"
    )
    assert provenance.snapshot_sha256(first) == provenance.snapshot_sha256(second)
    assert provenance.identity_key(first) == provenance.identity_key(second)


@pytest.mark.parametrize(
    "value",
    [
        "ftp://example.com/model",
        "https:///model",
        "https://user:secret@example.com/model",
    ],
)
def test_canonicalize_url_rejects_non_public_or_credentialed_urls(value: str) -> None:
    with pytest.raises(ValueError, match="canonical_url_must"):
        provenance.canonicalize_url(value)


def test_canonicalize_url_preserves_port_and_strips_query_and_fragment() -> None:
    assert (
        provenance.canonicalize_url(
            "HTTPS://Example.COM:8443/model/42?token=secret#details"
        )
        == "https://example.com:8443/model/42"
    )


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"source_filename": ""}, "invalid_source_filename"),
        ({"source_filename": "x" * 513}, "invalid_source_filename"),
        ({"blob_sha256": "not-a-sha"}, "invalid_blob_sha256"),
    ],
)
def test_provenance_context_rejects_malformed_identity_fields(
    overrides: dict[str, str], error: str
) -> None:
    values = {
        "manifest": _capture(),
        "source_file_id": "42:file-a",
        "source_filename": "part.stl",
        "blob_sha256": "a" * 64,
    }
    values.update(overrides)
    with pytest.raises(ValueError, match=error):
        provenance.ProvenanceContext(**values)


def test_capture_is_idempotent_and_override_empty_wins(db_session: Session) -> None:
    model = _model(db_session)
    initial = provenance.upsert_capture(
        db_session, model_id=model.id, manifest=_capture()
    )
    db_session.commit()
    duplicate = provenance.upsert_capture(
        db_session, model_id=model.id, manifest=_capture()
    )
    provenance.set_user_override(
        db_session, provenance_source_id=initial.source.id, field_name="title", value=""
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


def test_user_override_creates_field_when_capture_omits_allowlisted_field(
    db_session: Session,
) -> None:
    model = _model(db_session)
    actor = User(username="provenance-override-owner", hashed_password="not-used")
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


def test_clear_user_override_is_idempotent_for_absent_capture_field(
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
    assert row.user_updated_at is not None and row.user_updated_at >= user_updated_at


def test_clear_user_override_is_noop_when_field_row_is_absent(
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


def test_stable_source_id_promotes_and_merges_legacy_url_source(
    db_session: Session,
) -> None:
    model = _model(db_session)
    legacy = provenance.upsert_capture(
        db_session, model_id=model.id, manifest=_legacy_capture(title="Legacy title")
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
        select(ModelProvenanceSource).where(ModelProvenanceSource.model_id == model.id)
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


def test_source_merge_cover_delete_proof_failure_rolls_back_the_whole_merge(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
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

    with pytest.raises(UnsafeStorageDeleteError, match="storage_ownership_unverified"):
        provenance.upsert_capture(
            db_session, model_id=model.id, manifest=_capture(title="Stable update")
        )
    db_session.rollback()

    sources = db_session.exec(
        select(ModelProvenanceSource).where(ModelProvenanceSource.model_id == model.id)
    ).all()
    assert {source.id for source in sources} == {stable.source.id, legacy.source.id}
    assert {
        cover.provenance_source_id
        for cover in db_session.exec(select(ModelSourceCover))
    } == {stable.source.id, legacy.source.id}
    assert db_session.exec(select(StorageDeleteIntent)).all() == []
    assert backend.exists(target_key)
    assert backend.exists(obsolete_key)


def test_portable_attach_keeps_existing_capture_and_local_override(
    db_session: Session,
) -> None:
    model = _model(db_session)
    artifact = File(
        model_id=model.id,
        path="provenance/existing.stl",
        original_filename="existing.stl",
        file_type=FileType.STL,
        size_bytes=1,
        sha256="c" * 64,
    )
    db_session.add(artifact)
    db_session.flush()
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
        db_session, artifact, context, imported_overrides={"title": "Imported title"}
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


def test_attach_existing_rejects_unsupported_portable_override(
    db_session: Session,
) -> None:
    model = _model(db_session)
    artifact = File(
        model_id=model.id,
        path="provenance/unsupported.stl",
        original_filename="unsupported.stl",
        file_type=FileType.STL,
        size_bytes=1,
        sha256="e" * 64,
    )
    db_session.add(artifact)
    db_session.flush()
    context = provenance.ProvenanceContext(
        manifest=_capture(),
        source_file_id="42:file-a",
        source_filename="part.stl",
        blob_sha256="e" * 64,
    )

    with pytest.raises(ValueError, match="unsupported_provenance_field"):
        provenance.attach_existing_artifact(
            db_session, artifact, context, imported_overrides={"secret": "value"}
        )

    assert (
        db_session.exec(
            select(ModelProvenanceField).where(
                ModelProvenanceField.field_name == "secret"
            )
        ).all()
        == []
    )


def test_attach_existing_imports_override_for_sparse_capture(
    db_session: Session,
) -> None:
    model = _model(db_session)
    artifact = File(
        model_id=model.id,
        path="provenance/sparse.stl",
        original_filename="sparse.stl",
        file_type=FileType.STL,
        size_bytes=1,
        sha256="f" * 64,
    )
    db_session.add(artifact)
    db_session.flush()
    context = provenance.ProvenanceContext(
        manifest=_capture_without_title(),
        source_file_id="42:file-a",
        source_filename="part.stl",
        blob_sha256="f" * 64,
    )

    result = provenance.attach_existing_artifact(
        db_session, artifact, context, imported_overrides={"title": "Portable title"}
    )

    row = db_session.exec(
        select(ModelProvenanceField).where(
            ModelProvenanceField.provenance_source_id
            == result.link.provenance_source_id,
            ModelProvenanceField.field_name == "title",
        )
    ).one()
    assert result.imported_override_fields == ("title",)
    assert result.conflicting_override_fields == ()
    assert row.captured_at is None
    assert provenance.effective_value(row) == "Portable title"


def test_import_key_is_stable_and_distinguishes_blob_bytes() -> None:
    capture = _capture()
    assert provenance.import_key(
        capture,
        source_file_id="file-a",
        source_filename="part.stl",
        blob_sha256="b" * 64,
    ) == provenance.import_key(
        capture,
        source_file_id="file-a",
        source_filename="renamed.stl",
        blob_sha256="b" * 64,
    )
    assert provenance.import_key(
        capture,
        source_file_id="file-a",
        source_filename="part.stl",
        blob_sha256="b" * 64,
    ) != provenance.import_key(
        capture,
        source_file_id="file-a",
        source_filename="part.stl",
        blob_sha256="c" * 64,
    )


def test_inbox_result_stores_lowercase_state_values(db_session: Session) -> None:
    user = User(username="provenance-owner", hashed_password="not-used")
    db_session.add(user)
    db_session.flush()
    inbox = InboxItem(owner_user_id=user.id)
    db_session.add(inbox)
    db_session.flush()
    for index, state in enumerate(InboxItemResultState):
        row = InboxItemResult(
            inbox_item_id=inbox.id,
            source_selection_id=f"selection-{index}",
            result_key=f"key-{index}",
            original_filename="part.stl",
            state=state,
        )
        db_session.add(row)
    db_session.commit()
    assert [row.state for row in db_session.exec(select(InboxItemResult)).all()] == [
        "imported",
        "deduplicated",
        "failed",
    ]


def test_preflight_does_not_disclose_a_link_to_an_unrelated_actor(
    db_session: Session,
) -> None:
    model = _model(db_session)
    artifact = File(
        model_id=model.id,
        path="provenance/part.stl",
        original_filename="part.stl",
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
    actor = User(username="unrelated", hashed_password="not-used")
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


def test_preflight_without_blob_identity_returns_not_found_without_mutation(
    db_session: Session,
) -> None:
    before_models = len(db_session.exec(select(Model)).all())
    before_files = len(db_session.exec(select(File)).all())

    result = provenance.preflight_existing_artifact(
        db_session,
        provenance.ProvenanceContext(
            manifest=_capture(),
            source_file_id="42:file-a",
            source_filename="part.stl",
            blob_sha256=None,
            actor_id=None,
        ),
    )

    assert result.status == "not_found"
    assert result.link is result.model_id is result.file_id is None
    assert len(db_session.exec(select(Model)).all()) == before_models
    assert len(db_session.exec(select(File)).all()) == before_files


def test_attach_ingested_artifact_requires_blob_sha256(
    db_session: Session,
) -> None:
    model = _model(db_session)
    artifact = File(
        model_id=model.id,
        path="provenance/no-blob.stl",
        original_filename="no-blob.stl",
        file_type=FileType.STL,
        size_bytes=1,
        sha256="1" * 64,
    )
    db_session.add(artifact)
    db_session.flush()
    context = provenance.ProvenanceContext(
        manifest=_capture(),
        source_file_id="42:file-a",
        source_filename="part.stl",
        blob_sha256=None,
    )

    with pytest.raises(ValueError, match="provenance_context_requires_blob_sha256"):
        provenance.attach_ingested_artifact(db_session, artifact, context)

    assert db_session.exec(select(ArtifactProvenanceLink)).all() == []


def test_live_reuse_trash_restore_and_hard_delete_follow_provenance_lifecycle(
    db_session: Session,
) -> None:
    """Provenance follows its Model's lifecycle but never owns Artifact bytes."""
    model = _model(db_session)
    assert model.id is not None
    actor = User(
        username="provenance-admin",
        hashed_password="not-used",
        is_superuser=True,
    )
    artifact = File(
        model_id=model.id,
        path="external/provenance-part.stl",
        original_filename="part.stl",
        file_type=FileType.STL,
        size_bytes=1,
        sha256="d" * 64,
        is_external=True,
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
        provenance.preflight_existing_artifact(db_session, context).status == "reusable"
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
    db_session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")
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
