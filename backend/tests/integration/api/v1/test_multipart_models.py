"""Standalone multipart model API keeps Models addressable and reusable."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import CollectionRole, Document, File, FileType, MultipartModelChoice
from app.services import multipart_models


class TestMultipartModels:
    def test_full_save_updates_metadata_with_composition_atomically(
        self, client, auth_headers, make_model
    ) -> None:
        member = make_model("Atomic member")
        created = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Atomic assembly"},
        ).json()

        response = client.put(
            f"/api/v1/multipart-models/{created['id']}",
            headers=auth_headers,
            json={
                "name": "Saved assembly",
                "description": "A complete composition",
                "parts": [{"name": "Body", "choices": [{"model_id": member.id}]}],
            },
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Saved assembly"
        assert response.json()["slug"] == "saved-assembly"
        assert response.json()["description"] == "A complete composition"
        assert response.json()["parts"][0]["models"][0]["id"] == member.id

    def test_full_save_uses_selected_member_as_cover(
        self, client, auth_headers, db_session, make_model, make_file
    ) -> None:
        base = make_model("Cover base")
        handle = make_model("Cover handle")
        base_file = make_file(base, file_type=FileType.STL, filename="cover-base.stl")
        handle_file = make_file(
            handle, file_type=FileType.STL, filename="cover-handle.stl"
        )
        base.thumbnail_file_id = base_file.id
        handle.thumbnail_file_id = handle_file.id
        db_session.add(base)
        db_session.add(handle)
        db_session.commit()
        created = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Cover assembly"},
        ).json()

        response = client.put(
            f"/api/v1/multipart-models/{created['id']}",
            headers=auth_headers,
            json={
                "cover_model_id": handle.id,
                "parts": [
                    {"name": "Base", "choices": [{"model_id": base.id}]},
                    {"name": "Handle", "choices": [{"model_id": handle.id}]},
                ],
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["cover_model_id"] == handle.id
        assert response.json()["cover_thumbnail_url"] == (
            f"/api/v1/files/{handle_file.id}/thumbnail"
        )

    def test_full_save_rejects_cover_outside_composition(
        self, client, auth_headers, make_model
    ) -> None:
        member = make_model("Cover member")
        outsider = make_model("Cover outsider")
        created = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Invalid cover assembly"},
        ).json()

        response = client.put(
            f"/api/v1/multipart-models/{created['id']}",
            headers=auth_headers,
            json={
                "cover_model_id": outsider.id,
                "parts": [{"name": "Body", "choices": [{"model_id": member.id}]}],
            },
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "multipart_cover_requires_member"

    def test_full_save_clears_removed_legacy_cover(
        self, client, auth_headers, make_model
    ) -> None:
        first = make_model("Original cover")
        replacement = make_model("Replacement member")
        created = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Changing cover assembly"},
        ).json()
        saved = client.put(
            f"/api/v1/multipart-models/{created['id']}",
            headers=auth_headers,
            json={
                "cover_model_id": first.id,
                "parts": [{"name": "Body", "choices": [{"model_id": first.id}]}],
            },
        )
        assert saved.status_code == 200, saved.text

        response = client.put(
            f"/api/v1/multipart-models/{created['id']}",
            headers=auth_headers,
            json={
                "parts": [{"name": "Body", "choices": [{"model_id": replacement.id}]}]
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["cover_model_id"] is None

    def test_full_save_rejects_invalid_member_without_partial_metadata_change(
        self, client, auth_headers, make_model
    ) -> None:
        member = make_model("Stable atomic member")
        created = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Stable atomic assembly"},
        ).json()
        aggregate_id = created["id"]
        assert (
            client.put(
                f"/api/v1/multipart-models/{aggregate_id}",
                headers=auth_headers,
                json={
                    "name": "Before failed save",
                    "parts": [{"name": "Body", "choices": [{"model_id": member.id}]}],
                },
            ).status_code
            == 200
        )

        failed = client.put(
            f"/api/v1/multipart-models/{aggregate_id}",
            headers=auth_headers,
            json={
                "name": "Must not persist",
                "description": "Must not persist",
                "parts": [{"name": "Body", "choices": [{"model_id": 99999999}]}],
            },
        )

        assert failed.status_code == 400
        current = client.get(
            f"/api/v1/multipart-models/{aggregate_id}", headers=auth_headers
        ).json()
        assert current["name"] == "Before failed save"
        assert current["description"] is None
        assert current["parts"][0]["models"][0]["id"] == member.id

    def test_create_rejects_missing_collection(self, client, auth_headers) -> None:
        response = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Missing collection", "collection_id": 99999999},
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "collection_not_found"

    def test_create_rejects_whitespace_name(self, client, auth_headers) -> None:
        response = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "   "},
        )

        assert response.status_code == 422

    def test_create_rejects_duplicate_slug(self, client, auth_headers) -> None:
        first = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Same slug"},
        )
        second = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Same-slug"},
        )

        assert first.status_code == 201
        assert second.status_code == 409
        assert second.json()["detail"] == "multipart_model_slug_exists"

    def test_create_maps_slug_integrity_race_to_conflict(
        self, client, auth_headers, monkeypatch
    ) -> None:
        def fail_commit(_session: Session) -> None:
            raise IntegrityError("insert", {}, RuntimeError("duplicate"))

        monkeypatch.setattr(Session, "commit", fail_commit)
        response = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Raced slug"},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "multipart_model_slug_exists"

    def test_creates_empty_multipart_model(self, client, auth_headers) -> None:
        response = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Broom holder"},
        )

        assert response.status_code == 201
        assert response.json()["parts"] == []

    def test_composes_reusable_models_without_hiding_library_members(
        self, client, auth_headers, make_model
    ) -> None:
        base = make_model("Base")
        handle = make_model("Handle")
        aggregate = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Broom holder"},
        )
        assert aggregate.status_code == 201
        aggregate_id = aggregate.json()["id"]

        composed = client.put(
            f"/api/v1/multipart-models/{aggregate_id}/parts",
            headers=auth_headers,
            json={
                "parts": [
                    {"name": "Base", "model_ids": [base.id]},
                    {"name": "Handle", "model_ids": [handle.id]},
                ]
            },
        )
        assert composed.status_code == 200
        assert composed.json()["model_count"] == 2
        assert [part["name"] for part in composed.json()["parts"]] == [
            "Base",
            "Handle",
        ]

        library = client.get("/api/v1/models", headers=auth_headers)
        assert library.status_code == 200
        assert {item["name"] for item in library.json()} >= {
            "Base",
            "Handle",
        }

        second = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Broom holder variant"},
        )
        assert second.status_code == 201
        reused = client.put(
            f"/api/v1/multipart-models/{second.json()['id']}/parts",
            headers=auth_headers,
            json={"parts": [{"name": "Base", "model_ids": [base.id]}]},
        )
        assert reused.status_code == 200

    def test_alternatives_report_each_model_file_counts(
        self, client, auth_headers, make_model, make_file
    ) -> None:
        base = make_model("Base")
        short = make_model("Short handle")
        long = make_model("Long handle")
        make_file(base, file_type=FileType.STL, filename="base.stl")
        make_file(short, file_type=FileType.STL, filename="short.stl")
        make_file(short, file_type=FileType.GCODE, filename="short.gcode")
        make_file(long, file_type=FileType.STL, filename="long.stl")
        response = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Handle assembly"},
        )
        aggregate_id = response.json()["id"]
        response = client.put(
            f"/api/v1/multipart-models/{aggregate_id}/parts",
            headers=auth_headers,
            json={
                "parts": [
                    {"name": "Base", "model_ids": [base.id]},
                    {"name": "Handle", "model_ids": [short.id, long.id]},
                ]
            },
        )
        assert response.status_code == 200
        handle = response.json()["parts"][1]["models"]
        assert [
            (item["name"], item["source_file_count"], item["gcode_revision_count"])
            for item in handle
        ] == [
            ("Short handle", 1, 1),
            ("Long handle", 1, 0),
        ]

    def test_metadata_patch_is_returned_by_searchable_list(
        self, client, auth_headers
    ) -> None:
        created = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Original name"},
        )
        created_updated_at = datetime.fromisoformat(created.json()["updated_at"])
        aggregate_id = created.json()["id"]
        patched = client.patch(
            f"/api/v1/multipart-models/{aggregate_id}",
            headers=auth_headers,
            json={"name": "Renamed assembly", "description": "Two pieces"},
        )
        assert patched.status_code == 200
        assert patched.json()["description"] == "Two pieces"
        assert datetime.fromisoformat(patched.json()["updated_at"]) > created_updated_at
        listed = client.get("/api/v1/multipart-models?q=Renamed", headers=auth_headers)
        assert [item["id"] for item in listed.json()] == [aggregate_id]

    def test_patch_rejects_whitespace_name(self, client, auth_headers) -> None:
        created = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Patch name"},
        )
        response = client.patch(
            f"/api/v1/multipart-models/{created.json()['id']}",
            headers=auth_headers,
            json={"name": "   "},
        )

        assert response.status_code == 422
        assert response.json()["detail"] == "name_required"

    def test_patch_same_name_preserves_slug(self, client, auth_headers) -> None:
        created = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Stable name"},
        ).json()
        response = client.patch(
            f"/api/v1/multipart-models/{created['id']}",
            headers=auth_headers,
            json={"name": "Stable name"},
        )

        assert response.status_code == 200
        assert response.json()["slug"] == created["slug"]

    def test_patch_allows_a_name_change_with_the_same_slug(
        self, client, auth_headers
    ) -> None:
        created = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Stable punctuation"},
        ).json()

        response = client.patch(
            f"/api/v1/multipart-models/{created['id']}",
            headers=auth_headers,
            json={"name": "Stable-punctuation"},
        )

        assert response.status_code == 200
        assert response.json()["slug"] == created["slug"]

    def test_patch_rejects_duplicate_slug(self, client, auth_headers) -> None:
        first = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "First slug"},
        ).json()
        second = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Second slug"},
        ).json()
        response = client.patch(
            f"/api/v1/multipart-models/{second['id']}",
            headers=auth_headers,
            json={"name": first["name"]},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "multipart_model_slug_exists"

    def test_patch_maps_slug_integrity_race_to_conflict(
        self, client, auth_headers, monkeypatch
    ) -> None:
        created = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Patch race"},
        ).json()

        def fail_commit(_session: Session) -> None:
            raise IntegrityError("update", {}, RuntimeError("duplicate"))

        monkeypatch.setattr(Session, "commit", fail_commit)
        response = client.patch(
            f"/api/v1/multipart-models/{created['id']}",
            headers=auth_headers,
            json={"name": "Patch race renamed"},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "multipart_model_slug_exists"

    def test_parts_integrity_race_maps_to_invalid_request(
        self, client, auth_headers, monkeypatch
    ) -> None:
        created = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Parts race"},
        ).json()

        def fail_replace(*_args, **_kwargs):
            raise IntegrityError("insert", {}, RuntimeError("duplicate"))

        monkeypatch.setattr(multipart_models, "replace_parts", fail_replace)
        response = client.put(
            f"/api/v1/multipart-models/{created['id']}/parts",
            headers=auth_headers,
            json={"parts": []},
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "multipart_parts_invalid"

    def test_get_unknown_aggregate_returns_not_found(
        self, client, auth_headers
    ) -> None:
        response = client.get("/api/v1/multipart-models/99999999", headers=auth_headers)

        assert response.status_code == 404
        assert response.json()["detail"] == "multipart_model_not_found"

    def test_root_direct_filter_only_returns_root_aggregates(
        self, client, auth_headers, make_collection
    ) -> None:
        collection = make_collection("Scoped")
        root = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Root assembly"},
        ).json()
        nested = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Nested assembly", "collection_id": collection.id},
        ).json()

        listed = client.get(
            "/api/v1/multipart-models?direct=true", headers=auth_headers
        )

        assert {item["id"] for item in listed.json()} == {root["id"]}
        assert nested["id"] not in {item["id"] for item in listed.json()}

    def test_user_without_collection_access_sees_no_aggregates_or_candidates(
        self, client, auth_headers, make_collection, make_user, headers_for
    ) -> None:
        collection = make_collection("Private aggregate")
        aggregate = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Private assembly", "collection_id": collection.id},
        ).json()
        user = make_user("no-multipart-access")
        headers = headers_for(user)

        listed = client.get("/api/v1/multipart-models", headers=headers)
        candidates = client.get(
            f"/api/v1/multipart-models/{aggregate['id']}/candidates",
            headers=headers,
        )

        assert listed.status_code == 200
        assert listed.json() == []
        assert candidates.status_code == 403

    def test_create_rejects_viewer_for_collection(
        self, client, auth_headers, make_collection, make_user, headers_for, grant_role
    ) -> None:
        collection = make_collection("Viewer create collection")
        viewer = make_user("multipart-create-viewer")
        grant_role(viewer, collection, CollectionRole.VIEW)

        before = client.get("/api/v1/multipart-models", headers=auth_headers).json()
        response = client.post(
            "/api/v1/multipart-models",
            headers=headers_for(viewer),
            json={"name": "Denied assembly", "collection_id": collection.id},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "collection_permission_denied"
        after = client.get("/api/v1/multipart-models", headers=auth_headers).json()
        assert after == before

    def test_patch_rejects_viewer_for_aggregate(
        self, client, auth_headers, make_collection, make_user, headers_for, grant_role
    ) -> None:
        collection = make_collection("Viewer patch collection")
        aggregate = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Protected patch assembly", "collection_id": collection.id},
        ).json()
        viewer = make_user("multipart-patch-viewer")
        grant_role(viewer, collection, CollectionRole.VIEW)

        response = client.patch(
            f"/api/v1/multipart-models/{aggregate['id']}",
            headers=headers_for(viewer),
            json={"name": "Must not rename", "description": "Must not persist"},
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "collection_permission_denied"
        current = client.get(
            f"/api/v1/multipart-models/{aggregate['id']}", headers=auth_headers
        ).json()
        assert current["name"] == "Protected patch assembly"
        assert current["description"] is None

    def test_save_rejects_viewer_for_aggregate(
        self,
        client,
        auth_headers,
        make_collection,
        make_model,
        make_user,
        headers_for,
        grant_role,
    ) -> None:
        collection = make_collection("Viewer save collection")
        member = make_model("Protected save member")
        aggregate = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Protected save assembly", "collection_id": collection.id},
        ).json()
        assert (
            client.put(
                f"/api/v1/multipart-models/{aggregate['id']}",
                headers=auth_headers,
                json={
                    "parts": [{"name": "Body", "choices": [{"model_id": member.id}]}]
                },
            ).status_code
            == 200
        )
        viewer = make_user("multipart-save-viewer")
        grant_role(viewer, collection, CollectionRole.VIEW)

        response = client.put(
            f"/api/v1/multipart-models/{aggregate['id']}",
            headers=headers_for(viewer),
            json={
                "name": "Must not rename",
                "parts": [],
            },
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "collection_permission_denied"
        current = client.get(
            f"/api/v1/multipart-models/{aggregate['id']}", headers=auth_headers
        ).json()
        assert current["name"] == "Protected save assembly"
        assert current["parts"][0]["name"] == "Body"
        assert current["parts"][0]["models"][0]["id"] == member.id

    def test_delete_rejects_viewer_for_aggregate(
        self, client, auth_headers, make_collection, make_user, headers_for, grant_role
    ) -> None:
        collection = make_collection("Viewer delete collection")
        aggregate = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Protected delete assembly", "collection_id": collection.id},
        ).json()
        viewer = make_user("multipart-delete-viewer")
        grant_role(viewer, collection, CollectionRole.VIEW)

        response = client.delete(
            f"/api/v1/multipart-models/{aggregate['id']}",
            headers=headers_for(viewer),
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "collection_permission_denied"
        current = client.get(
            f"/api/v1/multipart-models/{aggregate['id']}", headers=auth_headers
        )
        assert current.status_code == 200
        assert current.json()["name"] == "Protected delete assembly"

    def test_candidates_filter_by_collection_access(
        self,
        client,
        auth_headers,
        make_model,
        make_collection,
        make_user,
        headers_for,
        grant_role,
    ) -> None:
        visible = make_collection("Candidate visible")
        hidden = make_collection("Candidate hidden")
        visible_model = make_model("Visible candidate", collection=visible)
        hidden_model = make_model("Hidden candidate", collection=hidden)
        aggregate = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Candidate scope", "collection_id": visible.id},
        ).json()
        viewer = make_user("candidate-viewer")
        grant_role(viewer, visible, CollectionRole.VIEW)

        response = client.get(
            f"/api/v1/multipart-models/{aggregate['id']}/candidates",
            headers=headers_for(viewer),
        )

        assert response.status_code == 200
        ids = {item["id"] for item in response.json()}
        assert visible_model.id in ids
        assert hidden_model.id not in ids

    def test_blank_candidate_query_keeps_candidates_visible(
        self, client, auth_headers, make_model
    ) -> None:
        member = make_model("Blank query candidate")
        aggregate = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Blank query assembly"},
        ).json()

        candidates = client.get(
            f"/api/v1/multipart-models/{aggregate['id']}/candidates?q=%20%20",
            headers=auth_headers,
        )

        assert member.id in {item["id"] for item in candidates.json()}

    def test_service_candidates_returns_empty_for_user_without_collections(
        self, db_session, make_user
    ) -> None:
        user = make_user("no-candidate-collections")

        assert multipart_models.candidates(db_session, user) == []

    def test_duplicate_member_rejection_keeps_existing_composition(
        self, client, auth_headers, make_model
    ) -> None:
        first = make_model("First")
        second = make_model("Second")
        created = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Stable assembly"},
        )
        aggregate_id = created.json()["id"]
        valid = {"parts": [{"name": "Main", "model_ids": [first.id]}]}
        assert (
            client.put(
                f"/api/v1/multipart-models/{aggregate_id}/parts",
                headers=auth_headers,
                json=valid,
            ).status_code
            == 200
        )
        invalid = client.put(
            f"/api/v1/multipart-models/{aggregate_id}/parts",
            headers=auth_headers,
            json={
                "parts": [
                    {"name": "Main", "model_ids": [first.id]},
                    {"name": "Other", "model_ids": [first.id, second.id]},
                ]
            },
        )
        assert invalid.status_code == 400
        assert (
            client.get(
                f"/api/v1/multipart-models/{aggregate_id}", headers=auth_headers
            ).json()["parts"][0]["models"][0]["id"]
            == first.id
        )

    def test_duplicate_part_name_is_rejected(
        self, client, auth_headers, make_model
    ) -> None:
        first = make_model("First")
        second = make_model("Second")
        created = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Duplicate names"},
        )
        response = client.put(
            f"/api/v1/multipart-models/{created.json()['id']}/parts",
            headers=auth_headers,
            json={
                "parts": [
                    {"name": "Handle", "model_ids": [first.id]},
                    {"name": " handle ", "model_ids": [second.id]},
                ]
            },
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "multipart_part_name_duplicate"

    def test_missing_member_is_rejected(self, client, auth_headers) -> None:
        created = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Missing member"},
        )
        response = client.put(
            f"/api/v1/multipart-models/{created.json()['id']}/parts",
            headers=auth_headers,
            json={"parts": [{"name": "Part", "model_ids": [99999999]}]},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "multipart_model_member_not_found"

    def test_candidates_include_a_model_reused_by_another_aggregate(
        self, client, auth_headers, make_model
    ) -> None:
        member = make_model("Reusable member")
        first = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "First assembly"},
        )
        assert (
            client.put(
                f"/api/v1/multipart-models/{first.json()['id']}/parts",
                headers=auth_headers,
                json={"parts": [{"name": "Shared", "model_ids": [member.id]}]},
            ).status_code
            == 200
        )
        second = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Second assembly"},
        )
        candidates = client.get(
            f"/api/v1/multipart-models/{second.json()['id']}/candidates",
            headers=auth_headers,
        )
        assert member.id in {item["id"] for item in candidates.json()}

    def test_collection_filter_respects_direct_scope(
        self, client, auth_headers, make_collection
    ) -> None:
        workshop = make_collection("Workshop")
        handles = make_collection("Handles", parent=workshop)
        created = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Nested assembly", "collection_id": handles.id},
        )
        aggregate_id = created.json()["id"]

        descendants = client.get(
            "/api/v1/multipart-models?collection=workshop", headers=auth_headers
        ).json()
        direct_parent = client.get(
            "/api/v1/multipart-models?collection=workshop&direct=true",
            headers=auth_headers,
        ).json()
        direct_child = client.get(
            "/api/v1/multipart-models?collection=workshop/handles&direct=true",
            headers=auth_headers,
        ).json()

        assert aggregate_id in {item["id"] for item in descendants}
        assert aggregate_id not in {item["id"] for item in direct_parent}
        assert aggregate_id in {item["id"] for item in direct_child}

    def test_inaccessible_member_is_redacted(
        self,
        client,
        auth_headers,
        db_session,
        make_model,
        make_file,
        make_collection,
        make_user,
        headers_for,
        grant_role,
    ) -> None:
        visible = make_collection("Visible")
        hidden = make_collection("Hidden")
        member = make_model("Private member", collection=hidden)
        source_file = make_file(member, filename="private-legacy-option.stl")
        aggregate = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Redacted assembly", "collection_id": visible.id},
        )
        assert (
            client.put(
                f"/api/v1/multipart-models/{aggregate.json()['id']}/parts",
                headers=auth_headers,
                json={"parts": [{"name": "Part", "model_ids": [member.id]}]},
            ).status_code
            == 200
        )
        owner_detail = client.get(
            f"/api/v1/multipart-models/{aggregate.json()['id']}",
            headers=auth_headers,
        ).json()
        choice = db_session.get(
            MultipartModelChoice,
            owner_detail["parts"][0]["models"][0]["choice_id"],
        )
        assert choice is not None
        choice.label = "Private legacy filename"
        choice.source_file_id = source_file.id
        db_session.add(choice)
        db_session.commit()
        viewer = make_user("multipart-viewer")
        grant_role(viewer, visible, CollectionRole.VIEW)
        detail = client.get(
            f"/api/v1/multipart-models/{aggregate.json()['id']}",
            headers=headers_for(viewer),
        )
        redacted = detail.json()["parts"][0]["models"][0]
        assert redacted == {
            "id": member.id,
            "choice_id": redacted["choice_id"],
            "legacy_label": None,
            "source_file_id": None,
            "name": None,
            "slug": None,
            "thumbnail_url": None,
            "source_file_count": 0,
            "gcode_revision_count": 0,
            "available": False,
        }

    def test_editor_can_echo_redacted_choice_without_model_access(
        self,
        client,
        auth_headers,
        make_model,
        make_collection,
        make_user,
        headers_for,
        grant_role,
    ) -> None:
        visible = make_collection("Redacted edit aggregate")
        hidden = make_collection("Redacted edit member")
        member = make_model("Redacted editable member", collection=hidden)
        aggregate = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Redacted editable assembly", "collection_id": visible.id},
        ).json()
        assert (
            client.put(
                f"/api/v1/multipart-models/{aggregate['id']}/parts",
                headers=auth_headers,
                json={"parts": [{"name": "Part", "model_ids": [member.id]}]},
            ).status_code
            == 200
        )

        editor = make_user("redacted-composition-editor")
        grant_role(editor, visible, CollectionRole.EDIT)
        editor_headers = headers_for(editor)
        detail = client.get(
            f"/api/v1/multipart-models/{aggregate['id']}", headers=editor_headers
        ).json()
        redacted = detail["parts"][0]["models"][0]

        saved = client.put(
            f"/api/v1/multipart-models/{aggregate['id']}",
            headers=editor_headers,
            json={
                "name": detail["name"],
                "description": detail["description"],
                "parts": [
                    {
                        "name": "Part",
                        "choices": [
                            {"model_id": member.id, "choice_id": redacted["choice_id"]}
                        ],
                    }
                ],
            },
        )

        assert saved.status_code == 200
        assert (
            saved.json()["parts"][0]["models"][0]["choice_id"] == redacted["choice_id"]
        )

    def test_redacted_editor_cannot_add_guessed_choice(
        self,
        client,
        auth_headers,
        make_model,
        make_collection,
        make_user,
        headers_for,
        grant_role,
    ) -> None:
        visible = make_collection("Guessed choice aggregate")
        hidden = make_collection("Guessed choice member")
        member = make_model("Guessed private member", collection=hidden)
        aggregate = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Guessed choice assembly", "collection_id": visible.id},
        ).json()
        editor = make_user("guessed-choice-editor")
        grant_role(editor, visible, CollectionRole.EDIT)

        response = client.put(
            f"/api/v1/multipart-models/{aggregate['id']}",
            headers=headers_for(editor),
            json={
                "parts": [
                    {
                        "name": "Part",
                        "choices": [{"model_id": member.id, "choice_id": 42424242}],
                    }
                ]
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "multipart_choice_not_found"

    def test_choice_from_another_aggregate_is_rejected(
        self, client, auth_headers, make_model
    ) -> None:
        member = make_model("Foreign choice member")
        first = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "First choice assembly"},
        ).json()
        assert (
            client.put(
                f"/api/v1/multipart-models/{first['id']}/parts",
                headers=auth_headers,
                json={
                    "parts": [{"name": "Part", "choices": [{"model_id": member.id}]}]
                },
            ).status_code
            == 200
        )
        choice_id = client.get(
            f"/api/v1/multipart-models/{first['id']}", headers=auth_headers
        ).json()["parts"][0]["models"][0]["choice_id"]
        second = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Second choice assembly"},
        ).json()

        response = client.put(
            f"/api/v1/multipart-models/{second['id']}",
            headers=auth_headers,
            json={
                "parts": [
                    {
                        "name": "Part",
                        "choices": [{"model_id": member.id, "choice_id": choice_id}],
                    }
                ]
            },
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "multipart_choice_not_found"

    def test_edit_requires_read_access_to_every_selected_model(
        self,
        client,
        auth_headers,
        make_model,
        make_collection,
        make_user,
        headers_for,
        grant_role,
    ) -> None:
        editable = make_collection("Editable assemblies")
        private = make_collection("Private members")
        member = make_model("Private choice", collection=private)
        aggregate = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Protected assembly", "collection_id": editable.id},
        )
        editor = make_user("multipart-editor")
        grant_role(editor, editable, CollectionRole.EDIT)

        denied = client.put(
            f"/api/v1/multipart-models/{aggregate.json()['id']}/parts",
            headers=headers_for(editor),
            json={"parts": [{"name": "Part", "model_ids": [member.id]}]},
        )

        assert denied.status_code == 403
        assert denied.json()["detail"] == "collection_permission_denied"
        assert (
            client.get(
                f"/api/v1/multipart-models/{aggregate.json()['id']}",
                headers=auth_headers,
            ).json()["parts"]
            == []
        )

    def test_trashed_member_is_redacted_without_removing_the_grouping(
        self, client, auth_headers, make_model, db_session
    ) -> None:
        member = make_model("Restorable choice")
        aggregate = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Restorable assembly"},
        )
        aggregate_id = aggregate.json()["id"]
        assert (
            client.put(
                f"/api/v1/multipart-models/{aggregate_id}/parts",
                headers=auth_headers,
                json={"parts": [{"name": "Part", "model_ids": [member.id]}]},
            ).status_code
            == 200
        )
        member.deleted_at = utcnow()
        db_session.add(member)
        db_session.commit()

        detail = client.get(
            f"/api/v1/multipart-models/{aggregate_id}", headers=auth_headers
        ).json()

        assert detail["parts"][0]["models"][0]["available"] is False
        assert detail["part_count"] == 1

    def test_delete_preserves_member_files(
        self, client, auth_headers, make_model, make_file, db_session
    ) -> None:
        member = make_model("Persistent member")
        file_row = make_file(member, file_type=FileType.STL, filename="member.stl")
        created = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Disposable grouping"},
        )
        aggregate_id = created.json()["id"]
        assert (
            client.put(
                f"/api/v1/multipart-models/{aggregate_id}/parts",
                headers=auth_headers,
                json={"parts": [{"name": "Part", "model_ids": [member.id]}]},
            ).status_code
            == 200
        )
        assert (
            client.delete(
                f"/api/v1/multipart-models/{aggregate_id}", headers=auth_headers
            ).status_code
            == 204
        )
        assert db_session.get(File, file_row.id) is not None
        assert member.id in {
            item["id"]
            for item in client.get("/api/v1/models", headers=auth_headers).json()
        }

    def test_delete_preserves_guides_as_detached_documents(
        self, client, auth_headers, db_session
    ) -> None:
        aggregate_id = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={"name": "Documented grouping"},
        ).json()["id"]
        guide = client.post(
            "/api/v1/documents/upload",
            data={"multipart_model_id": str(aggregate_id)},
            files={"file": ("assembly.md", b"# Assembly", "text/markdown")},
            headers=auth_headers,
        ).json()

        response = client.delete(
            f"/api/v1/multipart-models/{aggregate_id}", headers=auth_headers
        )

        assert response.status_code == 204
        db_session.expire_all()
        preserved = db_session.get(Document, guide["id"])
        assert preserved is not None
        assert preserved.multipart_model_id is None
        assert (
            client.get(
                f"/api/v1/documents/{guide['id']}", headers=auth_headers
            ).status_code
            == 200
        )
