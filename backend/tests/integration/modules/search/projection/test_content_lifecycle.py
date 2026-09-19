"""Real content owners publish transactionally through the neutral projection port."""

import hashlib

import pytest
from sqlmodel import select

from app.db.models import AuditLog, FileType, SearchDependency, SearchPassage
from app.db.projections import bind_content_projection, content_changed
from app.modules.administration.audit import install_audit_listeners
from app.modules.ingestion.ingestion import persist_artifact
from app.modules.library import commands, provenance, revisions, trash
from app.modules.search.projection import LibraryProjection
from app.schemas.models import (
    FileRevisionUpdate,
    ModelBatchMove,
    ModelBatchTags,
    TagSetUpdate,
)
from tests._env import use_local_storage
from tests.factories import bearer
from tests.search_projection import drain_search


@pytest.fixture(autouse=True)
def content_projection(tmp_path):
    use_local_storage(tmp_path)
    previous = bind_content_projection(LibraryProjection())
    yield
    bind_content_projection(previous)


@pytest.fixture
def actor(make_user):
    return make_user(superuser=True)


def passage_text(session, kind, id):
    return "\n".join(
        session.exec(
            select(SearchPassage.text).where(
                SearchPassage.subject_type == kind, SearchPassage.subject_id == id
            )
        ).all()
    )


class TestDocumentProjection:
    def test_replaces_a_document_body_after_edit(
        self, client, actor, db_session, make_document
    ):
        doc = make_document(body="Old body")
        content_changed(db_session, "document", [doc.id])
        response = client.put(
            f"/api/v1/documents/{doc.id}",
            headers=bearer(actor),
            json={"body": "New assembly steps"},
        )
        assert response.status_code == 200, response.text
        drain_search(db_session)
        assert (
            passage_text(db_session, "document", doc.id)
            == f"Title: {doc.name}\nBody: New assembly steps"
        )

    def test_removes_a_trashed_document(self, client, actor, db_session, make_document):
        doc = make_document()
        content_changed(db_session, "document", [doc.id])
        response = client.delete(f"/api/v1/documents/{doc.id}", headers=bearer(actor))
        assert response.status_code == 204, response.text
        drain_search(db_session)
        assert passage_text(db_session, "document", doc.id) == ""

    def test_reindexes_a_restored_document(
        self, client, actor, db_session, make_document
    ):
        doc = make_document(trashed=True, body="Restore this guide")
        response = client.post(
            f"/api/v1/documents/{doc.id}/restore", headers=bearer(actor)
        )
        assert response.status_code == 200, response.text
        drain_search(db_session)
        assert "Restore this guide" in passage_text(db_session, "document", doc.id)

    def test_removes_a_purged_document(self, client, actor, db_session, make_document):
        doc = make_document()
        identity = doc.id
        content_changed(db_session, "document", [identity])
        response = client.delete(f"/api/v1/documents/{identity}", headers=bearer(actor))
        assert response.status_code == 204, response.text
        response = client.delete(
            f"/api/v1/documents/{identity}/permanent", headers=bearer(actor)
        )
        assert response.status_code == 204, response.text
        drain_search(db_session)
        assert passage_text(db_session, "document", identity) == ""
        assert (
            db_session.exec(
                select(SearchDependency).where(
                    SearchDependency.subject_type == "document",
                    SearchDependency.subject_id == identity,
                )
            ).all()
            == []
        )

    def test_indexes_an_uploaded_markdown_document(self, client, actor, db_session):
        response = client.post(
            "/api/v1/documents/upload",
            headers=bearer(actor),
            files={
                "file": ("Assembly.md", b"Press the latch firmly.", "text/markdown")
            },
        )
        assert response.status_code == 201, response.text
        drain_search(db_session)
        assert (
            passage_text(db_session, "document", response.json()["id"])
            == "Title: Assembly\nBody: Press the latch firmly."
        )

    def test_indexes_an_uploaded_binary_filename(self, client, actor, db_session):
        response = client.post(
            "/api/v1/documents/upload",
            headers=bearer(actor),
            files={
                "file": (
                    "assembly-manual.pdf",
                    b"%PDF-1.4 private-binary",
                    "application/pdf",
                )
            },
        )
        assert response.status_code == 201, response.text
        drain_search(db_session)
        assert (
            passage_text(db_session, "document", response.json()["id"])
            == "Title: assembly-manual\nFiles: assembly-manual.pdf"
        )


class TestTaxonomyProjection:
    def test_refreshes_models_after_collection_rename(
        self, client, actor, db_session, make_collection, make_model
    ):
        root = make_collection("Old")
        child = make_collection("Child", parent=root)
        model = make_model(collection=child)
        content_changed(db_session, "model", [model.id])
        response = client.patch(
            f"/api/v1/collections/{root.id}",
            headers=bearer(actor),
            json={"name": "New"},
        )
        assert response.status_code == 200, response.text
        drain_search(db_session)
        assert "Collection: new/child" in passage_text(db_session, "model", model.id)

    def test_replaces_collection_readme(
        self, client, actor, db_session, make_collection
    ):
        collection = make_collection()
        response = client.put(
            f"/api/v1/collections/{collection.id}/readme",
            headers=bearer(actor),
            json={"readme": "Snap fit assembly"},
        )
        assert response.status_code == 200, response.text
        drain_search(db_session)
        assert "Description: Snap fit assembly" in passage_text(
            db_session, "collection", collection.id
        )

    def test_refreshes_models_after_collection_tags(
        self, client, actor, db_session, make_collection, make_model
    ):
        collection = make_collection()
        model = make_model(collection=collection)
        response = client.put(
            f"/api/v1/collections/{collection.id}/tags",
            headers=bearer(actor),
            json={"tags": ["flexible"]},
        )
        assert response.status_code == 200, response.text
        drain_search(db_session)
        assert "Tags: flexible" in passage_text(db_session, "model", model.id)

    def test_removes_deleted_tag_text(
        self, client, actor, db_session, make_model, make_tag, tag_model
    ):
        model = make_model()
        tag = make_tag("flexible")
        tag_model(model, tag)
        content_changed(db_session, "model", [model.id])
        response = client.delete(f"/api/v1/tags/{tag.id}", headers=bearer(actor))
        assert response.status_code == 204, response.text
        drain_search(db_session)
        assert "flexible" not in passage_text(db_session, "model", model.id)


class TestMultipartProjection:
    def test_indexes_a_new_multipart_model(self, client, actor, db_session):
        response = client.post(
            "/api/v1/multipart-models",
            headers=bearer(actor),
            json={"name": "Gear assembly"},
        )
        assert response.status_code == 201, response.text
        drain_search(db_session)
        assert (
            passage_text(db_session, "multipart_model", response.json()["id"])
            == "Title: Gear assembly"
        )

    def test_refreshes_multipart_metadata(
        self, client, actor, db_session, make_multipart_model
    ):
        aggregate = make_multipart_model()
        response = client.patch(
            f"/api/v1/multipart-models/{aggregate.id}",
            headers=bearer(actor),
            json={"name": "Gear assembly"},
        )
        assert response.status_code == 200, response.text
        drain_search(db_session)
        assert (
            passage_text(db_session, "multipart_model", aggregate.id)
            == "Title: Gear assembly"
        )

    def test_removes_a_deleted_multipart_model(
        self, client, actor, db_session, make_multipart_model
    ):
        aggregate = make_multipart_model()
        identity = aggregate.id
        content_changed(db_session, "multipart_model", [identity])
        response = client.delete(
            f"/api/v1/multipart-models/{identity}", headers=bearer(actor)
        )
        assert response.status_code == 204, response.text
        drain_search(db_session)
        assert passage_text(db_session, "multipart_model", identity) == ""


class TestModelProjection:
    def test_refreshes_batch_model_moves(
        self, actor, db_session, make_model, make_collection
    ):
        model = make_model()
        collection = make_collection("Assembly")
        commands.batch_move_models(
            ModelBatchMove(model_ids=[model.id], collection=collection.path),
            actor,
            db_session,
        )
        drain_search(db_session)
        assert "Collection: assembly" in passage_text(db_session, "model", model.id)

    def test_refreshes_batch_model_tags(self, actor, db_session, make_model):
        model = make_model()
        commands.batch_tag_models(
            ModelBatchTags(model_ids=[model.id], add=["flexible"]), actor, db_session
        )
        drain_search(db_session)
        assert "Tags: flexible" in passage_text(db_session, "model", model.id)

    def test_removes_a_trashed_model(self, db_session, make_model):
        model = make_model()
        content_changed(db_session, "model", [model.id])
        trash.soft_delete_model(db_session, model)
        drain_search(db_session)
        assert passage_text(db_session, "model", model.id) == ""

    def test_reindexes_a_restored_model(self, db_session, make_model):
        model = make_model(trashed=True)
        trash.restore_model(db_session, model)
        drain_search(db_session)
        assert passage_text(db_session, "model", model.id) == f"Title: {model.name}"

    def test_refreshes_revision_notes(self, actor, db_session, make_model, make_file):
        model = make_model()
        file = make_file(model, file_type=FileType.GCODE)
        commands.update_file_revision(
            model.id,
            file.id,
            FileRevisionUpdate(revision_notes="Use a brim"),
            actor,
            db_session,
        )
        drain_search(db_session)
        assert "Revisions: Use a brim" in passage_text(db_session, "model", model.id)

    def test_removes_a_trashed_revision_filename(
        self, actor, db_session, make_model, make_file
    ):
        model = make_model()
        file = make_file(model, file_type=FileType.GCODE, filename="obsolete.gcode")
        make_file(model, file_type=FileType.GCODE, filename="current.gcode")
        content_changed(db_session, "model", [model.id])
        revisions.remove_revision(db_session, actor, model.id, file.id)
        drain_search(db_session)
        assert "obsolete.gcode" not in passage_text(db_session, "model", model.id)
        drain_search(db_session)
        assert "current.gcode" in passage_text(db_session, "model", model.id)

    def test_refreshes_artifact_tags(self, actor, db_session, make_model, make_file):
        model = make_model()
        file = make_file(model)
        commands.replace_file_tags(
            model.id, file.id, TagSetUpdate(tags=["flexible"]), actor, db_session
        )
        drain_search(db_session)
        assert "Tags: flexible" in passage_text(db_session, "model", model.id)

    def test_refreshes_provenance_override(
        self, db_session, make_model, make_provenance_source
    ):
        model = make_model()
        source = make_provenance_source(model)
        provenance.set_user_override(
            db_session,
            provenance_source_id=source.id,
            field_name="title",
            value="Captured bracket",
        )
        db_session.commit()
        drain_search(db_session)
        assert "Source titles: Captured bracket" in passage_text(
            db_session, "model", model.id
        )

    def test_indexes_ingested_artifact_metadata(self, db_session, make_model, tmp_path):
        model = make_model()
        staged = tmp_path / "spring.stl"
        data = b"solid spring\nendsolid\n"
        staged.write_bytes(data)
        persist_artifact(
            db_session,
            model=model,
            staged_path=staged,
            original_filename=staged.name,
            file_type=FileType.STL,
            blob_hash=hashlib.sha256(data).hexdigest(),
            meta={},
            thumb_bytes=None,
            overwrite_thumbnail=False,
        )
        drain_search(db_session)
        assert "Files: spring.stl" in passage_text(db_session, "model", model.id)

    def test_omits_derived_passages_from_audit(self, db_session, make_model):
        install_audit_listeners()
        model = make_model("Private bracket")
        content_changed(db_session, "model", [model.id])
        db_session.commit()
        assert (
            db_session.exec(
                select(AuditLog).where(
                    AuditLog.resource_type.in_(
                        [
                            "search_passages",
                            "search_dependencies",
                            "search_reconciliation_states",
                        ]
                    )
                )
            ).all()
            == []
        )
