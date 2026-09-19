"""Search text comes from live library content and preserves contributor privacy.

The extraction contract is independent of similarity, inference and filesystem
parsers; heterogeneous Subjects never collapse their different access scopes.
"""

import json
from datetime import datetime

import pytest
from printstash_core.search.passages import MAX_FIELD_ITEMS, SearchSubject, SubjectType
from sqlmodel import select

from app.db.models import (
    SENTINEL_MODEL_HASH,
    DocumentKind,
    FileType,
    Model,
    MultipartModelChoice,
)
from app.modules.library.provenance import set_user_override
from app.modules.search.sources import project_subject


class TestProjectSubject:
    def test_projects_model_fields(
        self, db_session, make_model, make_file, make_collection
    ):
        collection = make_collection("Toys")
        model = make_model("Dragon", collection=collection, description="No supports")
        make_file(
            model,
            filename="dragon.gcode",
            file_type=FileType.GCODE,
            revision_label="Draft",
            revision_notes="Tested",
        )

        projection = project_subject(
            db_session, SearchSubject(SubjectType.MODEL, model.id)
        )

        content = projection.segments[0].content
        assert content.title == "Dragon"
        assert content.description == "No supports"
        assert content.collection == collection.path
        assert content.filenames == ("dragon.gcode",)
        assert content.revisions == ("Draft\nTested",)

    def test_uses_effective_tags(
        self,
        db_session,
        make_model,
        make_collection,
        make_file,
        make_tag,
        tag_model,
        tag_collection,
        tag_file,
    ):
        ancestor = make_collection("Toys")
        collection = make_collection("Dragons", parent=ancestor)
        model = make_model("Dragon", collection=collection)
        artifact = make_file(model)
        direct = make_tag("direct")
        inherited = make_tag("inherited")
        file_tag = make_tag("artifact")
        tag_model(model, direct)
        tag_collection(ancestor, inherited)
        tag_file(artifact, file_tag)
        tag_file(artifact, direct)

        projection = project_subject(
            db_session, SearchSubject(SubjectType.MODEL, model.id)
        )

        assert projection.segments[0].content.tags == (
            "artifact",
            "direct",
            "inherited",
        )

    def test_excludes_trashed_artifact_text(self, db_session, make_model, make_file):
        model = make_model("Dragon")
        make_file(
            model, filename="secret.gcode", revision_notes="Hidden notes", trashed=True
        )

        projection = project_subject(
            db_session, SearchSubject(SubjectType.MODEL, model.id)
        )

        assert projection.segments[0].content.filenames == ()
        assert projection.segments[0].content.revisions == ()

    def test_ignores_nonrevision_notes(self, db_session, make_model, make_file):
        model = make_model("Dragon")
        make_file(model, file_type=FileType.STL, revision_notes="Not a Revision")

        projection = project_subject(
            db_session, SearchSubject(SubjectType.MODEL, model.id)
        )

        assert projection.segments[0].content.revisions == ()

    def test_honors_provenance_overrides(
        self, db_session, make_model, make_provenance_source
    ):
        model = make_model("Dragon")
        source = make_provenance_source(model, tags=["toy"])
        set_user_override(
            db_session,
            provenance_source_id=source.id,
            field_name="title",
            value="Local title",
        )
        set_user_override(
            db_session,
            provenance_source_id=source.id,
            field_name="description",
            value="Local summary",
        )
        db_session.flush()

        projection = project_subject(
            db_session, SearchSubject(SubjectType.MODEL, model.id)
        )

        content = projection.segments[0].content
        assert content.source_titles == ("Local title",)
        assert content.source_summaries == ("Local summary",)
        assert content.source_tags == ("toy",)

    def test_honors_cleared_provenance_text(
        self, db_session, make_model, make_provenance_source
    ):
        model = make_model("Dragon")
        source = make_provenance_source(model)
        field = set_user_override(
            db_session, provenance_source_id=source.id, field_name="title", value=None
        )
        field.captured_value_json = json.dumps("Suppressed title")
        db_session.add(field)
        db_session.flush()

        projection = project_subject(
            db_session, SearchSubject(SubjectType.MODEL, model.id)
        )

        assert projection.segments[0].content.source_titles == ()

    def test_reports_excess_source_tags(
        self, db_session, make_model, make_provenance_source
    ):
        model = make_model("Dragon")
        make_provenance_source(model, tags=["toy"] * (MAX_FIELD_ITEMS + 1))

        projection = project_subject(
            db_session, SearchSubject(SubjectType.MODEL, model.id)
        )

        assert projection.segments[0].truncated

    @pytest.mark.parametrize(
        "encoded, expected",
        [('["toy", null, 42]', ("toy",)), ('{"unexpected": "shape"}', ())],
        ids=["nontext-entries", "nonlist-value"],
    )
    def test_ignores_nontext_provenance_tags(
        self, db_session, make_model, make_provenance_source, encoded, expected
    ):
        model = make_model("Dragon")
        make_provenance_source(model, tags_json=encoded)

        projection = project_subject(
            db_session, SearchSubject(SubjectType.MODEL, model.id)
        )

        assert projection.segments[0].content.source_tags == expected

    def test_projects_collection_fields(self, db_session, make_collection):
        collection = make_collection("Toys", readme="# Printing tips")

        projection = project_subject(
            db_session, SearchSubject(SubjectType.COLLECTION, collection.id)
        )

        content = projection.segments[0].content
        assert content.title == "Toys"
        assert content.collection == collection.path
        assert content.description == "# Printing tips"

    def test_projects_multipart_parts(
        self, db_session, make_multipart_model, make_multipart_part
    ):
        multipart = make_multipart_model("Box", description="Sliding lid")
        make_multipart_part(multipart, name="Lid")

        projection = project_subject(
            db_session, SearchSubject(SubjectType.MULTIPART_MODEL, multipart.id)
        )

        content = projection.segments[0].content
        assert content.title == "Box"
        assert content.description == "Sliding lid"
        assert content.parts == ("Lid",)

    def test_segments_multipart_member_context(
        self, db_session, client, auth_headers, make_model, make_collection
    ):
        restricted = make_collection("Private")
        model = make_model("Unreleased bracket", collection=restricted)
        created = client.post(
            "/api/v1/multipart-models", headers=auth_headers, json={"name": "Desk"}
        )
        assert created.status_code == 201, created.text
        response = client.put(
            f"/api/v1/multipart-models/{created.json()['id']}",
            headers=auth_headers,
            json={"parts": [{"name": "Leg", "choices": [{"model_id": model.id}]}]},
        )
        assert response.status_code == 200, response.text
        choice = db_session.exec(select(MultipartModelChoice)).one()
        choice.label = "Prototype"
        db_session.add(choice)
        db_session.flush()

        projection = project_subject(
            db_session,
            SearchSubject(SubjectType.MULTIPART_MODEL, response.json()["id"]),
        )

        shared, member = projection.segments
        assert shared.content.choices == ()
        assert shared.access_dependencies == ()
        assert member.content.choices == ("Unreleased bracket", "Prototype")
        assert member.access_dependencies == (
            SearchSubject(SubjectType.MODEL, model.id),
        )

    def test_projects_markdown_body(self, db_session, make_document):
        document = make_document("Guide", body="# Assembly\nFit the lid.")

        projection = project_subject(
            db_session, SearchSubject(SubjectType.DOCUMENT, document.id)
        )

        assert projection.segments[0].content.body == "# Assembly\nFit the lid."
        assert projection.segments[0].content.filenames == ()

    @pytest.mark.parametrize(
        "kind",
        [kind for kind in DocumentKind if kind != DocumentKind.MARKDOWN],
        ids=lambda kind: kind.value,
    )
    def test_indexes_only_binary_document_metadata(
        self, db_session, make_document, kind
    ):
        document = make_document(
            "Manual", kind=kind, filename="manual.pdf", body="Must not extract this"
        )

        projection = project_subject(
            db_session, SearchSubject(SubjectType.DOCUMENT, document.id)
        )

        assert projection.segments[0].content.title == "Manual"
        assert projection.segments[0].content.filenames == ("manual.pdf",)
        assert projection.segments[0].content.body == ""

    @pytest.mark.parametrize(
        "kind,fixture,overrides",
        [
            (SubjectType.MODEL, "make_model", {"trashed": True}),
            (
                SubjectType.COLLECTION,
                "make_collection",
                {"deleted_at": datetime(2026, 1, 1)},
            ),
            (SubjectType.DOCUMENT, "make_document", {"trashed": True}),
        ],
        ids=["model", "collection", "document"],
    )
    def test_excludes_trashed_subjects(
        self, db_session, request, kind, fixture, overrides
    ):
        row = request.getfixturevalue(fixture)(**overrides)

        assert project_subject(db_session, SearchSubject(kind, row.id)) is None

    @pytest.mark.parametrize("kind", list(SubjectType), ids=lambda kind: kind.value)
    def test_omits_missing_subjects(self, db_session, kind):
        assert project_subject(db_session, SearchSubject(kind, 123456)) is None

    def test_excludes_the_external_print_sentinel(self, db_session):
        sentinel = db_session.exec(
            select(Model).where(Model.hash == SENTINEL_MODEL_HASH)
        ).one()

        assert (
            project_subject(db_session, SearchSubject(SubjectType.MODEL, sentinel.id))
            is None
        )
