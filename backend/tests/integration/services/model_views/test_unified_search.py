"""Unified library search and effective-tag semantics."""

from __future__ import annotations

from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import FileType
from app.schemas.models import ModelFilters
from app.schemas.multipart_models import MultipartPartWrite
from app.services import model_views, multipart_models
from tests.factories import (
    build_collection,
    build_file,
    build_model,
    build_multipart_model,
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


def _assert_member_model_remains_visible_after_composition(
    db_session: Session,
) -> None:
    user = build_user(db_session, "assembly-search", superuser=True)
    assembly = build_multipart_model(db_session, "Desk lamp")
    shade = build_model(db_session, "Voronoi shade")
    build_file(db_session, shade, filename="shade.stl")
    multipart_models.replace_parts(
        db_session,
        user,
        assembly,
        [MultipartPartWrite(name="Shade", model_ids=[shade.id])],
    )

    assert shade.id in _ids(db_session, user)
    assert shade.id in _ids(db_session, user, q="Voronoi shade")


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

    def test_member_models_remain_visible_after_composition(
        self, db_session: Session
    ) -> None:
        _assert_member_model_remains_visible_after_composition(db_session)
