from __future__ import annotations

from sqlmodel import Session

from app.db.models import File, FileRevisionStatus, FileType, Metadata, Model, User
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
    row = File(
        model_id=model.id,
        path=filename,
        original_filename=filename,
        file_type=file_type,
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
    _artifact(db_session, model, filename="two.stl", file_type=FileType.STL, material="PLA")
    result = model_views.facets(db_session, user, ModelFilters())
    assert next(item.count for item in result.material_type if item.value == "PLA") == 1
