"""Defends snapshot hash is canonical and identity uses stable source id at the services provenance integration boundary.

A regression could attach source identity or portable metadata to the wrong artifact.
"""

from __future__ import annotations

from ._provenance_shared import (
    ArtifactProvenanceLink,
    File,
    FileType,
    MagicMock,
    ModelProvenanceField,
    ModelProvenanceSource,
    ModelSourceCover,
    ProvenanceCapture,
    Session,
    StorageBackend,
    StorageDeleteIntent,
    UnsafeStorageDeleteError,
    User,
    _capture,
    _capture_without_title,
    _legacy_capture,
    _model,
    get_backend,
    json,
    provenance,
    pytest,
    record_creation,
    select,
    storage_deletion,
)


def test_snapshot_hash_is_canonical_and_identity_uses_stable_source_id() -> None:
    first = _capture()
    second = _capture()

    assert (
        provenance.canonicalize_url(first.source.canonical_url)
        == "https://printables.com/model/42"
    )
    assert provenance.snapshot_sha256(first) == provenance.snapshot_sha256(second)
    assert provenance.identity_key(first) == provenance.identity_key(second)


def test_attach_existing_artifact_persists_one_link_to_its_capture(
    db_session: Session,
) -> None:
    model = _model(db_session)
    artifact = File(
        model_id=model.id,
        path="provenance/linked.stl",
        original_filename="linked.stl",
        file_type=FileType.STL,
        size_bytes=1,
        sha256="b" * 64,
    )
    db_session.add(artifact)
    db_session.flush()
    context = provenance.ProvenanceContext(
        manifest=_capture(),
        source_file_id="42:file-a",
        source_filename="linked.stl",
        blob_sha256=artifact.sha256,
    )

    result = provenance.attach_existing_artifact(db_session, artifact, context)
    db_session.commit()

    link = db_session.exec(
        select(ArtifactProvenanceLink).where(
            ArtifactProvenanceLink.file_id == artifact.id
        )
    ).one()
    capture = db_session.exec(
        select(ProvenanceCapture).where(
            ProvenanceCapture.provenance_source_id == link.provenance_source_id
        )
    ).one()
    assert link.provenance_source_id == result.link.provenance_source_id
    assert capture.source_revision is None


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
