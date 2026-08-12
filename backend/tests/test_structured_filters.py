from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session

from app.db.models import (
    Collection,
    File,
    FileRevisionStatus,
    FileType,
    Metadata,
    Model,
    Printer,
    PrinterFile,
    PrintJob,
    PrintJobState,
    Tag,
    User,
)
from app.schemas.models import ModelFilters
from app.services import model_views


def _model(session: Session, name: str, suffix: str) -> Model:
    row = Model(name=name, slug=name.lower(), hash=suffix * 64)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _artifact(
    session: Session,
    model: Model,
    *,
    filename: str,
    file_type: FileType,
    material: str | None = None,
    status: FileRevisionStatus | None = None,
) -> File:
    version = model.next_file_version
    model.next_file_version += 1
    row = File(
        model_id=model.id,
        path=filename,
        original_filename=filename,
        file_type=file_type,
        version=version,
        revision_status=status,
        size_bytes=1,
        sha256=(filename[0] * 64),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    session.add(Metadata(file_id=row.id, material_type=material))
    session.commit()
    return row


def test_structured_filters_require_metadata_on_same_artifact(db_session: Session) -> None:
    user = User(username="filter-admin", hashed_password="x", is_superuser=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    split = _model(db_session, "Split", "c")
    _artifact(db_session, split, filename="split.stl", file_type=FileType.STL, material="PLA")
    _artifact(
        db_session,
        split,
        filename="split.gcode",
        file_type=FileType.GCODE,
        material="PETG",
        status=FileRevisionStatus.KNOWN_GOOD,
    )
    same = _model(db_session, "Same", "d")
    _artifact(
        db_session,
        same,
        filename="same.gcode",
        file_type=FileType.GCODE,
        material="PLA",
        status=FileRevisionStatus.KNOWN_GOOD,
    )

    rows = model_views.list_items(
        db_session,
        user,
        filters=ModelFilters(
            file_type=[FileType.GCODE],
            material_type=["PLA"],
            revision_status=[FileRevisionStatus.KNOWN_GOOD],
        ),
    )
    assert [row.id for row in rows] == [same.id]


def test_facets_count_distinct_models(db_session: Session) -> None:
    user = User(username="facet-admin", hashed_password="x", is_superuser=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    model = _model(db_session, "Facet", "e")
    _artifact(db_session, model, filename="one.stl", file_type=FileType.STL, material="PLA")
    _artifact(
        db_session,
        model,
        filename="two.stl",
        file_type=FileType.STL,
        material="PLA",
        status=FileRevisionStatus.KNOWN_GOOD,
    )
    result = model_views.facets(db_session, user, ModelFilters())
    assert next(item.count for item in result.material_type if item.value == "PLA") == 1
    assert [item.model_dump() for item in result.file_type] == [
        {"value": "stl", "count": 1}
    ]
    assert [item.model_dump() for item in result.revision_status] == [
        {"value": "known_good", "count": 1}
    ]
    assert [item.model_dump() for item in result.storage] == [
        {"value": "vault", "count": 1}
    ]
    assert [item.model_dump() for item in result.printed] == [
        {"value": "yes", "count": 0},
        {"value": "no", "count": 1},
    ]


def _admin(db_session: Session, username: str) -> User:
    user = User(username=username, hashed_password="x", is_superuser=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


# --------------------------------------------------------------------------- #
# _apply_structured_filters — storage / uploaded window / slicer / printer_model
# --------------------------------------------------------------------------- #


def test_storage_filter_single_value_matches_external_only(db_session: Session) -> None:
    user = _admin(db_session, "storage-admin")
    external = _model(db_session, "External", "1")
    _artifact(db_session, external, filename="e.stl", file_type=FileType.STL)
    vault = _model(db_session, "Vault", "2")
    _artifact(db_session, vault, filename="v.stl", file_type=FileType.STL)
    ext_file = db_session.exec(
        __import__("sqlmodel").select(File).where(File.model_id == external.id)
    ).first()
    ext_file.is_external = True
    db_session.add(ext_file)
    db_session.commit()

    rows = model_views.list_items(
        db_session, user, filters=ModelFilters(storage=["external"])
    )
    assert [row.id for row in rows] == [external.id]


def test_uploaded_after_and_before_bound_the_window(db_session: Session) -> None:
    user = _admin(db_session, "uploaded-admin")
    early = _model(db_session, "Early", "3")
    late = _model(db_session, "Late", "4")
    early_file = _artifact(db_session, early, filename="early.stl", file_type=FileType.STL)
    late_file = _artifact(db_session, late, filename="late.stl", file_type=FileType.STL)
    early_file.uploaded_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
    late_file.uploaded_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
    db_session.add(early_file)
    db_session.add(late_file)
    db_session.commit()

    after = model_views.list_items(
        db_session,
        user,
        filters=ModelFilters(uploaded_after=datetime(2025, 1, 1, tzinfo=timezone.utc)),
    )
    assert [row.id for row in after] == [late.id]

    before = model_views.list_items(
        db_session,
        user,
        filters=ModelFilters(uploaded_before=datetime(2025, 1, 1, tzinfo=timezone.utc)),
    )
    assert [row.id for row in before] == [early.id]


def test_slicer_name_and_printer_model_filters(db_session: Session) -> None:
    user = _admin(db_session, "slicer-admin")
    orca = _model(db_session, "Orca", "5")
    prusa = _model(db_session, "Prusa", "6")
    orca_file = _artifact(db_session, orca, filename="orca.gcode", file_type=FileType.GCODE)
    prusa_file = _artifact(db_session, prusa, filename="prusa.gcode", file_type=FileType.GCODE)
    orca_meta = db_session.exec(
        __import__("sqlmodel").select(Metadata).where(Metadata.file_id == orca_file.id)
    ).first()
    orca_meta.slicer_name = "OrcaSlicer"
    orca_meta.printer_model = "Voron 2.4"
    prusa_meta = db_session.exec(
        __import__("sqlmodel").select(Metadata).where(Metadata.file_id == prusa_file.id)
    ).first()
    prusa_meta.slicer_name = "PrusaSlicer"
    prusa_meta.printer_model = "MK4"
    db_session.add(orca_meta)
    db_session.add(prusa_meta)
    db_session.commit()

    by_slicer = model_views.list_items(
        db_session, user, filters=ModelFilters(slicer_name=["orcaslicer"])
    )
    assert [row.id for row in by_slicer] == [orca.id]

    by_printer = model_views.list_items(
        db_session, user, filters=ModelFilters(printer_model=["mk4"])
    )
    assert [row.id for row in by_printer] == [prusa.id]


def test_printed_true_and_false_filters(db_session: Session) -> None:
    user = _admin(db_session, "printed-admin")
    printed = _model(db_session, "Printed", "7")
    unprinted = _model(db_session, "Unprinted", "8")
    printed_file = _artifact(db_session, printed, filename="p.gcode", file_type=FileType.GCODE)
    db_session.add(
        PrintJob(
            model_id=printed.id,
            file_id=printed_file.id,
            remote_filename="p.gcode",
            state=PrintJobState.COMPLETED,
        )
    )
    db_session.commit()

    was_printed = model_views.list_items(db_session, user, filters=ModelFilters(printed=True))
    assert [row.id for row in was_printed] == [printed.id]

    never_printed = model_views.list_items(db_session, user, filters=ModelFilters(printed=False))
    assert unprinted.id in [row.id for row in never_printed]
    assert printed.id not in [row.id for row in never_printed]


def test_print_outcome_filter(db_session: Session) -> None:
    user = _admin(db_session, "outcome-admin")
    failed = _model(db_session, "FailedModel", "9")
    completed = _model(db_session, "CompletedModel", "a")
    failed_file = _artifact(db_session, failed, filename="f.gcode", file_type=FileType.GCODE)
    completed_file = _artifact(db_session, completed, filename="c.gcode", file_type=FileType.GCODE)
    db_session.add(
        PrintJob(
            model_id=failed.id,
            file_id=failed_file.id,
            remote_filename="f.gcode",
            state=PrintJobState.FAILED,
        )
    )
    db_session.add(
        PrintJob(
            model_id=completed.id,
            file_id=completed_file.id,
            remote_filename="c.gcode",
            state=PrintJobState.COMPLETED,
        )
    )
    db_session.commit()

    rows = model_views.list_items(
        db_session, user, filters=ModelFilters(print_outcome=[PrintJobState.FAILED])
    )
    assert [row.id for row in rows] == [failed.id]


# --------------------------------------------------------------------------- #
# _filtered_stmt — direct/indirect collection scoping, tags, printer presence
# --------------------------------------------------------------------------- #


def test_direct_filter_with_collection_restricts_to_exact_path(db_session: Session) -> None:
    user = _admin(db_session, "direct-admin")
    parent = Collection(name="Parent", slug="parent", path="parent")
    child = Collection(name="Child", slug="child", path="parent/child")
    db_session.add(parent)
    db_session.add(child)
    db_session.commit()
    db_session.refresh(parent)
    db_session.refresh(child)
    direct_model = Model(name="Direct", slug="direct", hash="b" * 64, collection_id=parent.id)
    nested_model = Model(name="Nested", slug="nested", hash="c" * 64, collection_id=child.id)
    db_session.add(direct_model)
    db_session.add(nested_model)
    db_session.commit()
    db_session.refresh(direct_model)

    rows = model_views.list_items(
        db_session, user, filters=ModelFilters(collection="parent", direct=True)
    )
    assert [row.id for row in rows] == [direct_model.id]


def test_direct_filter_without_collection_matches_uncategorised_only(db_session: Session) -> None:
    user = _admin(db_session, "direct-admin2")
    col = Collection(name="Cat", slug="cat", path="cat")
    db_session.add(col)
    db_session.commit()
    db_session.refresh(col)
    categorised = Model(name="Categorised", slug="categorised", hash="d" * 64, collection_id=col.id)
    uncategorised = Model(name="Uncategorised", slug="uncategorised", hash="e" * 64)
    db_session.add(categorised)
    db_session.add(uncategorised)
    db_session.commit()
    db_session.refresh(uncategorised)

    rows = model_views.list_items(db_session, user, filters=ModelFilters(direct=True))
    ids = {row.id for row in rows}
    assert uncategorised.id in ids
    assert categorised.id not in ids


def test_indirect_collection_filter_includes_descendants(db_session: Session) -> None:
    user = _admin(db_session, "indirect-admin")
    parent = Collection(name="Parent2", slug="parent2", path="parent2")
    child = Collection(name="Child2", slug="child2", path="parent2/child2")
    db_session.add(parent)
    db_session.add(child)
    db_session.commit()
    db_session.refresh(parent)
    db_session.refresh(child)
    direct_model = Model(name="Direct2", slug="direct2", hash="f" * 64, collection_id=parent.id)
    nested_model = Model(name="Nested2", slug="nested2", hash="1" * 64, collection_id=child.id)
    db_session.add(direct_model)
    db_session.add(nested_model)
    db_session.commit()

    rows = model_views.list_items(db_session, user, filters=ModelFilters(collection="parent2"))
    ids = {row.id for row in rows}
    assert direct_model.id in ids
    assert nested_model.id in ids


def test_tag_filter_matches_by_slug(db_session: Session) -> None:
    user = _admin(db_session, "tag-admin")
    tagged = _model(db_session, "Tagged", "2")
    untagged = _model(db_session, "Untagged", "3")
    tag = Tag(name="Functional", slug="functional")
    db_session.add(tag)
    db_session.commit()
    db_session.refresh(tag)
    from app.db.models import ModelTagLink

    db_session.add(ModelTagLink(model_id=tagged.id, tag_id=tag.id))
    db_session.commit()

    rows = model_views.list_items(db_session, user, filters=ModelFilters(tag=["functional"]))
    ids = {row.id for row in rows}
    assert tagged.id in ids
    assert untagged.id not in ids


def test_printer_presence_any_matches_models_present_on_a_printer(db_session: Session) -> None:
    user = _admin(db_session, "presence-admin")
    present = _model(db_session, "Present", "4")
    absent = _model(db_session, "Absent", "5")
    present_file = _artifact(db_session, present, filename="present.gcode", file_type=FileType.GCODE)
    printer = Printer(name="Fleet1", moonraker_url="http://10.0.0.1:7125")
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    db_session.add(
        PrinterFile(printer_id=printer.id, file_id=present_file.id, remote_filename="present.gcode")
    )
    db_session.commit()

    rows = model_views.list_items(
        db_session, user, filters=ModelFilters(printer_presence="any")
    )
    ids = {row.id for row in rows}
    assert present.id in ids
    assert absent.id not in ids


# --------------------------------------------------------------------------- #
# list_items — mesh preview file id (first live mesh file wins)
# --------------------------------------------------------------------------- #


def test_list_items_picks_newest_version_mesh_file_for_preview(db_session: Session) -> None:
    user = _admin(db_session, "mesh-admin")
    model = _model(db_session, "MeshPreview", "6")
    v1 = File(
        model_id=model.id, path="v1.stl", original_filename="v1.stl", file_type=FileType.STL,
        version=1, size_bytes=1, sha256="1" * 64,
    )
    v2 = File(
        model_id=model.id, path="v2.stl", original_filename="v2.stl", file_type=FileType.STL,
        version=2, size_bytes=1, sha256="2" * 64,
    )
    db_session.add(v1)
    db_session.add(v2)
    db_session.commit()
    db_session.refresh(v2)

    rows = model_views.list_items(db_session, user, limit=100)
    row = next(r for r in rows if r.id == model.id)
    assert row.mesh_file_id == v2.id


# --------------------------------------------------------------------------- #
# export_payload — empty result, and populated collection/tag aggregation
# --------------------------------------------------------------------------- #


def test_export_payload_with_no_accessible_models_is_empty(db_session: Session) -> None:
    from app.db.models import SENTINEL_MODEL_HASH

    user = User(username="export-empty-admin", hashed_password="x", is_superuser=True)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    # A fresh in-memory DB may only have the sentinel model, which is excluded.
    from sqlmodel import select

    live_models = db_session.exec(
        select(Model).where(Model.deleted_at.is_(None), Model.hash != SENTINEL_MODEL_HASH)
    ).all()
    for m in live_models:
        m.deleted_at = datetime.now(timezone.utc)
        db_session.add(m)
    db_session.commit()

    payload = model_views.export_payload(db_session, user)

    assert payload["models"] == []
    assert payload["counts"]["files"] == 0


def test_export_payload_includes_collection_and_tag_names(db_session: Session) -> None:
    user = _admin(db_session, "export-admin")
    col = Collection(name="ExportCol", slug="export-col", path="export-col")
    db_session.add(col)
    db_session.commit()
    db_session.refresh(col)
    model = Model(name="Exportable", slug="exportable", hash="7" * 64, collection_id=col.id)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    tag = Tag(name="Neat", slug="neat")
    db_session.add(tag)
    db_session.commit()
    db_session.refresh(tag)
    from app.db.models import ModelTagLink

    db_session.add(ModelTagLink(model_id=model.id, tag_id=tag.id))
    _artifact(db_session, model, filename="exportable.stl", file_type=FileType.STL)
    db_session.commit()

    payload = model_views.export_payload(db_session, user)

    row = next(m for m in payload["models"] if m["id"] == model.id)
    assert row["collection"] == "export-col"
    assert row["tags"] == ["Neat"]


# --------------------------------------------------------------------------- #
# print_statistics — invalid period falls back to "30d"
# --------------------------------------------------------------------------- #


def test_print_statistics_invalid_period_defaults_to_30d(db_session: Session) -> None:
    result = model_views.print_statistics(db_session, "not-a-real-period")
    assert result.period == "30d"
