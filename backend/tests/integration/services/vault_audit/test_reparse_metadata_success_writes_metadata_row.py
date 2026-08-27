"""Defends reparse metadata success writes metadata row at the services vault audit integration boundary.

A regression could miss corruption or repair ownership and metadata incorrectly.
"""

from __future__ import annotations

from ._vault_audit_internals_shared import (
    Collection,
    Document,
    DocumentKind,
    File,
    FileType,
    Session,
    _make_file,
    _make_model,
    _patch_exec_injecting_unpersisted_row,
    _StubStrategy,
    get_backend,
    ownership_snapshot,
    pytest,
    vault_audit,
)


def test_reparse_metadata_success_writes_metadata_row(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = _make_model(db_session, "reparse-ok")
    file_row = _make_file(
        db_session, model, path="reparse-ok.gcode", file_type=FileType.GCODE
    )
    get_backend().write_bytes(b"G28\n", file_row.path)

    monkeypatch.setattr(
        "app.services.ingestion._gcode_strategy", lambda: _StubStrategy()
    )

    result = vault_audit._reparse_metadata(db_session, file_row.id)

    assert result is True
    from sqlmodel import select

    from app.db.models import Metadata

    meta = db_session.exec(
        select(Metadata).where(Metadata.file_id == file_row.id)
    ).first()
    assert meta is not None
    assert meta.material_type == "PLA"


def test_reparse_metadata_missing_blob_returns_false(db_session: Session) -> None:
    model = _make_model(db_session, "reparse-missing-blob")
    file_row = _make_file(
        db_session,
        model,
        path="does-not-exist-in-backend.gcode",
        file_type=FileType.GCODE,
    )

    result = vault_audit._reparse_metadata(db_session, file_row.id)

    assert result is False


def test_reparse_metadata_missing_file_row_returns_false(db_session: Session) -> None:
    assert vault_audit._reparse_metadata(db_session, 999999) is False


def test_reparse_metadata_already_has_metadata_is_a_noop_success(
    db_session: Session,
) -> None:
    from app.db.models import Metadata

    model = _make_model(db_session, "reparse-has-meta")
    file_row = _make_file(
        db_session, model, path="reparse-has-meta.gcode", file_type=FileType.GCODE
    )
    db_session.add(Metadata(file_id=file_row.id, material_type="PETG"))
    db_session.commit()

    result = vault_audit._reparse_metadata(db_session, file_row.id)

    assert result is True


def test_ownership_snapshot_skips_file_row_with_no_id(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = _make_model(db_session, "no-id-file")
    _make_file(db_session, model, path="persisted.stl")
    unpersisted = File(
        model_id=model.id,
        path="ghost.stl",
        original_filename="ghost.stl",
        file_type=FileType.STL,
    )
    assert unpersisted.id is None
    _patch_exec_injecting_unpersisted_row(monkeypatch, db_session, File, unpersisted)

    result = ownership_snapshot(db_session, discover=False)

    primary_keys = {blob.key for blob in result.primary}
    derived_keys = {blob.key for blob in result.derived}
    assert "persisted.stl" in primary_keys
    assert "ghost.stl" not in primary_keys
    assert not any(blob.resource_id is None for blob in result.derived)
    assert derived_keys  # the persisted file still contributed thumbnail/stl-cache keys


def test_ownership_snapshot_skips_document_row_with_no_id(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    persisted = Document(
        name="real-doc", kind=DocumentKind.MARKDOWN, filename="real.md"
    )
    db_session.add(persisted)
    db_session.commit()
    db_session.refresh(persisted)
    unpersisted = Document(
        name="ghost-doc", kind=DocumentKind.MARKDOWN, filename="ghost.md"
    )
    assert unpersisted.id is None
    _patch_exec_injecting_unpersisted_row(
        monkeypatch, db_session, Document, unpersisted
    )

    result = ownership_snapshot(db_session, discover=False)

    primary_names = {
        blob.display_name for blob in result.primary if blob.resource_type == "document"
    }
    assert "real.md" in primary_names
    assert "ghost.md" not in primary_names


def test_ownership_snapshot_document_embedded_image_id_must_match_row(
    db_session: Session,
) -> None:
    other = Document(name="other-doc", kind=DocumentKind.MARKDOWN)
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    owner = Document(
        name="owner-doc",
        kind=DocumentKind.MARKDOWN,
        body=f"![pic](/documents/{other.id}/images/stolen.png)",
    )
    db_session.add(owner)
    db_session.commit()
    db_session.refresh(owner)

    result = ownership_snapshot(db_session, discover=False)

    embedded_keys = {blob.key for blob in result.embedded}
    stolen_key = get_backend().document_image_key(other.id, "stolen.png")
    assert stolen_key not in embedded_keys

    owner.body = f"![pic](/documents/{owner.id}/images/mine.png)"
    db_session.add(owner)
    db_session.commit()

    result2 = ownership_snapshot(db_session, discover=False)
    matching = [
        blob
        for blob in result2.embedded
        if blob.resource_type == "document_image" and blob.resource_id == owner.id
    ]
    assert len(matching) == 1
    assert matching[0].key == get_backend().document_image_key(owner.id, "mine.png")
    assert matching[0].display_name == "mine.png"


def test_ownership_snapshot_skips_collection_row_with_no_id(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    persisted = Collection(name="real-col", slug="real-col", path="real-col")
    db_session.add(persisted)
    db_session.commit()
    db_session.refresh(persisted)
    unpersisted = Collection(
        name="ghost-col",
        slug="ghost-col",
        path="ghost-col",
        readme="![pic](/collections/999999/images/never.png)",
    )
    assert unpersisted.id is None
    _patch_exec_injecting_unpersisted_row(
        monkeypatch, db_session, Collection, unpersisted
    )

    result = ownership_snapshot(db_session, discover=False)

    assert not any(blob.resource_type == "collection_image" for blob in result.embedded)
