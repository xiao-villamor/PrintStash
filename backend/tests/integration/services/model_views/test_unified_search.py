"""Unified library search and effective-tag semantics."""

from __future__ import annotations

from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import FileType
from app.schemas.models import ModelFilters, PartGroupWrite, PartOptionWrite
from app.services import model_views, part_options
from tests.factories import (
    build_collection,
    build_file,
    build_model,
    build_tag,
    build_user,
    tag_collection,
    tag_file,
    tag_model,
)


def _ids(session: Session, user, **kwargs) -> set[int]:
    return {row.id for row in model_views.list_items(session, user, **kwargs)}


def _assert_search_matches_model_description_plus_live_artifact_filename(
    db_session: Session,
) -> None:
    user = build_user(db_session, "unified-search", superuser=True)
    described = build_model(db_session, "Plain", description="Parametric cable guide")
    artifact_model = build_model(db_session, "Unremarkable")
    live_file = build_file(
        db_session,
        artifact_model,
        filename="moon-lamp-diffuser.3mf",
        file_type=FileType.THREE_MF,
    )

    assert described.id in _ids(db_session, user, q="cable guide")
    assert artifact_model.id in _ids(db_session, user, q="moon-lamp")

    live_file.deleted_at = utcnow()
    db_session.add(live_file)
    db_session.commit()
    assert artifact_model.id not in _ids(db_session, user, q="moon-lamp")


def _assert_search_matches_collection_ancestor_plus_effective_tag(
    db_session: Session,
) -> None:
    user = build_user(db_session, "collection-search", superuser=True)
    parent = build_collection(db_session, "Workshop")
    child = build_collection(db_session, "Fixtures", parent=parent)
    model = build_model(db_session, "Clamp", collection=child)
    tag = build_tag(db_session, "Calibration")
    tag_collection(db_session, parent, tag)

    assert model.id in _ids(db_session, user, q="workshop")
    assert model.id in _ids(db_session, user, q="calibration")
    assert model.id in _ids(db_session, user, filters=ModelFilters(tag=["calibration"]))


def _assert_tag_filters_require_every_slug_across_sources(
    db_session: Session,
) -> None:
    user = build_user(db_session, "and-tags", superuser=True)
    collection = build_collection(db_session, "Tagged collection")
    matching = build_model(db_session, "Complete", collection=collection)
    incomplete = build_model(db_session, "Incomplete", collection=collection)
    artifact = build_file(db_session, matching, filename="complete.gcode")
    direct = build_tag(db_session, "Direct")
    inherited = build_tag(db_session, "Inherited")
    artifact_tag = build_tag(db_session, "Artifact")
    tag_model(db_session, matching, direct)
    tag_model(db_session, incomplete, direct)
    tag_collection(db_session, collection, inherited)
    tag_file(db_session, artifact, artifact_tag)

    rows = _ids(
        db_session,
        user,
        filters=ModelFilters(tag=["direct", "inherited", "artifact"]),
    )

    assert rows == {matching.id}


def _assert_trashed_artifact_tag_no_longer_applies(db_session: Session) -> None:
    user = build_user(db_session, "trashed-artifact-tags", superuser=True)
    model = build_model(db_session, "Tagged by file")
    artifact = build_file(db_session, model, filename="tagged.stl")
    tag = build_tag(db_session, "Temporary")
    tag_file(db_session, artifact, tag)
    assert model.id in _ids(db_session, user, filters=ModelFilters(tag=["temporary"]))

    artifact.deleted_at = utcnow()
    db_session.add(artifact)
    db_session.commit()

    assert model.id not in _ids(
        db_session, user, filters=ModelFilters(tag=["temporary"])
    )


def _assert_search_matches_part_group_plus_option_name(db_session: Session) -> None:
    user = build_user(db_session, "part-option-search", superuser=True)
    model = build_model(db_session, "Configurable bracket")
    narrow = build_file(db_session, model, filename="a.stl")
    wide = build_file(db_session, model, filename="b.stl")
    part_options.replace_for_model(
        db_session,
        model.id,
        [
            PartGroupWrite(
                name="Mounting width",
                options=[
                    PartOptionWrite(file_id=narrow.id, name="Narrow", is_default=True),
                    PartOptionWrite(file_id=wide.id, name="Wide stance"),
                ],
            )
        ],
    )

    assert model.id in _ids(db_session, user, q="mounting width")
    assert model.id in _ids(db_session, user, q="wide stance")

    wide.deleted_at = utcnow()
    db_session.add(wide)
    db_session.commit()
    assert model.id not in _ids(db_session, user, q="mounting width")


class TestUnifiedSearch:
    def test_search_matches_model_fields(self, db_session: Session) -> None:
        _assert_search_matches_model_description_plus_live_artifact_filename(db_session)

    def test_search_matches_inherited_context(self, db_session: Session) -> None:
        _assert_search_matches_collection_ancestor_plus_effective_tag(db_session)

    def test_tag_filters_require_every_slug_across_sources(
        self, db_session: Session
    ) -> None:
        _assert_tag_filters_require_every_slug_across_sources(db_session)

    def test_trashed_artifact_tag_no_longer_applies(self, db_session: Session) -> None:
        _assert_trashed_artifact_tag_no_longer_applies(db_session)

    def test_search_matches_part_option_context(self, db_session: Session) -> None:
        _assert_search_matches_part_group_plus_option_name(db_session)
