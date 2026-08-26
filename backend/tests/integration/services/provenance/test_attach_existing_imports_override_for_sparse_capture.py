"""Defends attach existing imports override for sparse capture at the services provenance integration boundary.

A regression could attach source identity or portable metadata to the wrong artifact.
"""

from __future__ import annotations

from ._provenance_shared import (
    ArtifactProvenanceLink,
    File,
    FileType,
    InboxItem,
    InboxItemResult,
    InboxItemResultState,
    Model,
    ModelProvenanceField,
    ModelProvenanceSource,
    ProvenanceCapture,
    Session,
    StorageDeleteIntent,
    User,
    _capture,
    _capture_without_title,
    _model,
    provenance,
    pytest,
    select,
    trash,
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
