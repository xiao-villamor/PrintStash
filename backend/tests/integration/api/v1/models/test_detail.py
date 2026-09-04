"""Reading one model and editing it: name, description, tags, collection, source URL.

The interesting half of the PATCH endpoint is where a model is allowed to *go*. Moving it
into a collection someone can already edit is an ordinary edit; moving it to the **root**,
or into a collection that does not exist yet (which creates it), are both library-shaping
acts and are superuser-only. The two refusals say different things —
`root_collection_admin_required` versus `collection_permission_denied` — because a user
who was told "you may not create collections" will go and ask for the collection, and one
told "you may not use the root" will not.

Tags are replaced wholesale rather than merged, so sending an empty list clears them.
That is what makes the tag editor's "remove the last tag" work at all.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import CollectionRole


class TestGetModel:
    def test_returns_the_model(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        model = make_model("Bracket")

        response = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["id"] == model.id

    def test_reports_a_model_that_does_not_exist(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.get("/api/v1/models/999999", headers=auth_headers)

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "model_not_found"

    def test_hides_a_trashed_model(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        from app.core.time import utcnow

        model = make_model("Trashed", deleted_at=utcnow())

        response = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)

        assert response.status_code == 404, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, make_model
    ) -> None:
        model = make_model("Anonymous")

        assert client.get(f"/api/v1/models/{model.id}").status_code == 401


class TestUpdateModel:
    def test_renames_the_model(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        model = make_model("Bracket")

        response = client.patch(
            f"/api/v1/models/{model.id}",
            headers=auth_headers,
            json={"name": "Renamed"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["name"] == "Renamed"

    def test_records_a_description(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        model = make_model("Bracket")

        response = client.patch(
            f"/api/v1/models/{model.id}",
            headers=auth_headers,
            json={"description": "Holds the shelf up"},
        )

        assert response.json()["description"] == "Holds the shelf up"

    def test_replaces_the_tags_it_is_given(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        model = make_model("Bracket")

        response = client.patch(
            f"/api/v1/models/{model.id}",
            headers=auth_headers,
            json={"tags": ["functional", "bracket"]},
        )

        assert set(response.json()["tags"]) == {"functional", "bracket"}

    def test_clears_the_tags_when_given_an_empty_list(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        model = make_model("Bracket")
        client.patch(
            f"/api/v1/models/{model.id}",
            headers=auth_headers,
            json={"tags": ["functional"]},
        )

        response = client.patch(
            f"/api/v1/models/{model.id}", headers=auth_headers, json={"tags": []}
        )

        # Tags are replaced, not merged — this is "remove the last tag".
        assert response.json()["tags"] == []

    def test_clears_the_source_url(
        self, client: TestClient, db_session: Session, auth_headers, make_model
    ) -> None:
        model = make_model("Bracket", source_url="https://example.com/original")

        response = client.patch(
            f"/api/v1/models/{model.id}",
            headers=auth_headers,
            json={"source_url": None},
        )

        assert response.json()["source_url"] is None

    def test_records_a_source_url(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        model = make_model("Benchy")

        response = client.patch(
            f"/api/v1/models/{model.id}",
            headers=auth_headers,
            json={"source_url": " https://www.printables.com/model/123-benchy "},
        )

        assert response.status_code == 200, response.text
        assert response.json()["source_url"] == (
            "https://www.printables.com/model/123-benchy"
        )

    def test_shows_the_source_url_on_the_model(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        model = make_model("Benchy")
        client.patch(
            f"/api/v1/models/{model.id}",
            headers=auth_headers,
            json={"source_url": "https://www.printables.com/model/123-benchy"},
        )

        detail = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)

        assert detail.json()["source_url"] == (
            "https://www.printables.com/model/123-benchy"
        )

    def test_carries_the_source_url_into_the_export(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        model = make_model("Benchy")
        client.patch(
            f"/api/v1/models/{model.id}",
            headers=auth_headers,
            json={"source_url": "https://www.printables.com/model/123-benchy"},
        )

        export = client.get("/api/v1/models/export", headers=auth_headers)

        # Where a model came from is the part of the export people care about.
        assert export.json()["models"][0]["source_url"] == (
            "https://www.printables.com/model/123-benchy"
        )

    def test_clears_the_source_url_when_given_an_empty_string(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        model = make_model("Benchy", source_url="https://example.com/original")

        response = client.patch(
            f"/api/v1/models/{model.id}", headers=auth_headers, json={"source_url": ""}
        )

        assert response.json()["source_url"] is None

    def test_rejects_a_source_url_that_is_not_http(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        model = make_model("Benchy")

        response = client.patch(
            f"/api/v1/models/{model.id}",
            headers=auth_headers,
            json={"source_url": "ftp://example.com/model.zip"},
        )

        assert response.status_code == 422, response.text

    def test_moves_the_model_into_an_existing_collection(
        self, client: TestClient, auth_headers, make_model, make_collection
    ) -> None:
        model = make_model("Bracket")
        collection = make_collection("Brackets")

        response = client.patch(
            f"/api/v1/models/{model.id}",
            headers=auth_headers,
            json={"collection": "brackets"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["collection_id"] == collection.id

    def test_creates_a_collection_that_does_not_exist_yet_for_a_superuser(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        model = make_model("Bracket")

        response = client.patch(
            f"/api/v1/models/{model.id}",
            headers=auth_headers,
            json={"collection": "brand/new/path"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["collection"] == "brand/new/path"

    def test_moves_the_model_to_the_root_for_a_superuser(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        make_model,
        make_collection,
    ) -> None:
        collection = make_collection("Brackets")
        model = make_model("Bracket", collection_id=collection.id)

        response = client.patch(
            f"/api/v1/models/{model.id}", headers=auth_headers, json={"collection": ""}
        )

        assert response.status_code == 200, response.text
        assert response.json()["collection"] is None

    def test_refuses_a_move_to_the_root_from_a_collection_editor(
        self,
        client: TestClient,
        make_model,
        make_collection,
        make_user,
        headers_for,
        grant_role,
    ) -> None:
        collection = make_collection("Brackets")
        model = make_model("Bracket", collection_id=collection.id)
        editor = make_user("root-mover")
        grant_role(editor, collection, CollectionRole.EDIT)

        response = client.patch(
            f"/api/v1/models/{model.id}",
            headers=headers_for(editor),
            json={"collection": ""},
        )

        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "root_collection_admin_required"

    def test_refuses_to_create_a_collection_for_a_collection_editor(
        self,
        client: TestClient,
        make_model,
        make_collection,
        make_user,
        headers_for,
        grant_role,
    ) -> None:
        collection = make_collection("Brackets")
        model = make_model("Bracket", collection_id=collection.id)
        editor = make_user("collection-creator")
        grant_role(editor, collection, CollectionRole.EDIT)

        response = client.patch(
            f"/api/v1/models/{model.id}",
            headers=headers_for(editor),
            json={"collection": "does/not/exist"},
        )

        # A different message from the root refusal, because the user's next
        # move is different: ask for the collection, not for admin.
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == "collection_permission_denied"

    def test_reports_a_model_that_does_not_exist(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.patch(
            "/api/v1/models/999999", headers=auth_headers, json={"name": "Ghost"}
        )

        assert response.status_code == 404, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, make_model
    ) -> None:
        model = make_model("Bracket")

        response = client.patch(
            f"/api/v1/models/{model.id}", json={"name": "Anonymous"}
        )

        assert response.status_code == 401, response.text


class TestExportModels:
    def test_exports_metadata_without_any_blobs(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        make_file(make_model("Bracket"))

        response = client.get("/api/v1/models/export", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["contents"]["kind"] == "metadata_only"

    def test_counts_what_it_exported(
        self, client: TestClient, auth_headers, make_model, make_file
    ) -> None:
        make_file(make_model("Bracket"))

        response = client.get("/api/v1/models/export", headers=auth_headers)

        assert response.json()["counts"] == {"models": 1, "files": 1}

    def test_carries_each_files_revision_record(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        make_model,
        make_file,
    ) -> None:
        row = make_file(make_model("Bracket"))
        row.revision_label = "PETG baseline"
        row.revision_status = "known_good"
        row.is_recommended = True
        db_session.add(row)
        db_session.commit()

        response = client.get("/api/v1/models/export", headers=auth_headers)

        exported = response.json()["models"][0]["files"][0]
        assert exported["revision_label"] == "PETG baseline"
        assert exported["revision_status"] == "known_good"
        assert exported["is_recommended"] is True

    def test_carries_each_files_slicer_metadata(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        make_model,
        make_file,
    ) -> None:
        from app.db.models import Metadata

        row = make_file(make_model("Bracket"))
        db_session.add(
            Metadata(
                file_id=row.id,
                slicer_name="OrcaSlicer",
                printer_model="Voron 2.4",
                layer_height_mm=0.2,
                material_type="PETG",
            )
        )
        db_session.commit()

        response = client.get("/api/v1/models/export", headers=auth_headers)

        exported = response.json()["models"][0]["files"][0]["metadata"]
        assert exported["slicer_name"] == "OrcaSlicer"
        assert exported["printer_model"] == "Voron 2.4"

    def test_flattens_to_one_csv_row_per_file(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers,
        make_model,
        make_file,
    ) -> None:
        from app.db.models import Metadata

        row = make_file(make_model("Bracket"))
        db_session.add(
            Metadata(file_id=row.id, slicer_name="PrusaSlicer", infill_percent=20)
        )
        db_session.commit()

        response = client.get("/api/v1/models/export?format=csv", headers=auth_headers)

        assert response.headers["content-type"].startswith("text/csv")
        assert "Bracket" in response.text
        assert "PrusaSlicer" in response.text

    def test_offers_the_export_as_a_download(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        make_model("Bracket")

        response = client.get("/api/v1/models/export", headers=auth_headers)

        assert response.headers["content-disposition"].startswith("attachment")

    def test_rejects_a_format_it_does_not_know(
        self, client: TestClient, auth_headers
    ) -> None:
        response = client.get("/api/v1/models/export?format=xml", headers=auth_headers)

        assert response.status_code == 422, response.text

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/models/export").status_code == 401


class TestVaultStats:
    def test_summarizes_the_library(
        self, client: TestClient, auth_headers, make_model
    ) -> None:
        make_model("Bracket")

        response = client.get("/api/v1/models/stats", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["model_count"] >= 1

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/models/stats").status_code == 401
