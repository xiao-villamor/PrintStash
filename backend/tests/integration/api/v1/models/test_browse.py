"""Authoritative mixed cards preserve membership, server order and refresh boundaries."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.db.models import CollectionRole, FileType, PrintJobState

BROWSE = "/api/v1/models/browse"
TIED = datetime(2026, 1, 1, tzinfo=timezone.utc)


class TestBrowseModels:
    def test_returns_mixed_cards(
        self, client, auth_headers, make_model, make_multipart_model
    ):
        model = make_model("Part")
        group = make_multipart_model("Set")
        response = client.put(
            f"/api/v1/multipart-models/{group.id}/parts",
            json={"parts": [{"name": "Body", "model_ids": [model.id]}]},
            headers=auth_headers,
        )
        assert response.status_code == 200, response.text

        response = client.get(BROWSE, headers=auth_headers)

        assert response.status_code == 200, response.text
        page = response.json()
        assert {
            (entry["kind"], entry[entry["kind"]]["id"]) for entry in page["items"]
        } == {("model", model.id), ("multipart", group.id)}
        assert page["total"] == 2
        assert isinstance(page["browse_revision"], str)

    def test_returns_only_multipart_sets(
        self, client, auth_headers, make_model, make_multipart_model
    ):
        make_model()
        group = make_multipart_model()

        response = client.get(
            BROWSE, params={"view": "multipart"}, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert [
            (row["kind"], row["multipart"]["id"]) for row in response.json()["items"]
        ] == [("multipart", group.id)]

    def test_orders_name_ties_by_kind_then_id(
        self, client, auth_headers, make_model, make_multipart_model
    ):
        first = make_model("Same")
        second = make_model("SAME")
        group = make_multipart_model("same")

        response = client.get(
            BROWSE, params={"sort": "name-desc"}, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert [
            (row["kind"], row[row["kind"]]["id"]) for row in response.json()["items"]
        ] == [("model", first.id), ("model", second.id), ("multipart", group.id)]

    def test_preserves_unicode_name_order(
        self, client, auth_headers, make_model, make_multipart_model
    ):
        upper = make_model("Éclair")
        lower = make_model("éclair")
        ascii_group = make_multipart_model("Zebra")

        response = client.get(BROWSE, params={"sort": "name-asc"}, headers=auth_headers)

        assert response.status_code == 200, response.text
        assert [row[row["kind"]]["name"] for row in response.json()["items"]] == [
            ascii_group.name,
            upper.name,
            lower.name,
        ]

    def test_requires_refresh_after_committed_write(
        self, client, auth_headers, make_model
    ):
        model = make_model("First")
        make_model("Second")
        first = client.get(BROWSE, params={"limit": 1}, headers=auth_headers)
        assert first.status_code == 200, first.text
        response = client.patch(
            f"/api/v1/models/{model.id}", json={"name": "Changed"}, headers=auth_headers
        )
        assert response.status_code == 200, response.text

        response = client.get(
            BROWSE,
            params={"limit": 1, "cursor": first.json()["next_cursor"]},
            headers=auth_headers,
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "browse_refresh_required"

    @pytest.mark.parametrize(
        "changed",
        [
            {"view": "multipart"},
            {"sort": "name-asc"},
            {"limit": 2},
            {"q": "First"},
            {"favorites": True},
        ],
        ids=["view", "sort", "limit", "query", "favorites"],
    )
    def test_rejects_rebound_cursor(self, client, auth_headers, make_model, changed):
        make_model("First")
        make_model("Second")
        first = client.get(BROWSE, params={"limit": 1}, headers=auth_headers)
        assert first.status_code == 200, first.text

        response = client.get(
            BROWSE,
            params={"limit": 1, "cursor": first.json()["next_cursor"], **changed},
            headers=auth_headers,
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "browse_cursor_invalid"

    @pytest.mark.parametrize(
        "cursor", ["bad", "e30=.bad", "!"], ids=["malformed", "unsigned", "alphabet"]
    )
    def test_rejects_tampered_cursor(self, client, auth_headers, cursor):
        response = client.get(BROWSE, params={"cursor": cursor}, headers=auth_headers)

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "browse_cursor_invalid"

    def test_excludes_trashed_models(self, client, auth_headers, make_model):
        make_model(trashed=True)

        response = client.get(BROWSE, headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["items"] == []

    def test_excludes_trashed_collections(
        self, client, auth_headers, make_collection, make_model, make_multipart_model
    ):
        folder = make_collection("Trash", trashed=True)
        make_model(collection=folder)
        make_multipart_model(collection=folder)

        response = client.get(BROWSE, headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["items"] == []

    def test_excludes_unreadable_entries(
        self,
        client,
        make_user,
        headers_for,
        make_collection,
        make_model,
        make_multipart_model,
    ):
        user = make_user()
        private = make_collection("Private")
        make_model(collection=private)
        make_multipart_model(collection=private)

        response = client.get(BROWSE, headers=headers_for(user))

        assert response.status_code == 200, response.text
        assert response.json()["items"] == []

    def test_filters_groups_by_matching_member(
        self, client, auth_headers, make_model, make_file, make_multipart_model
    ):
        member = make_model()
        make_file(member, file_type=FileType.GCODE)
        group = make_multipart_model()
        response = client.put(
            f"/api/v1/multipart-models/{group.id}/parts",
            json={"parts": [{"name": "Body", "model_ids": [member.id]}]},
            headers=auth_headers,
        )
        assert response.status_code == 200, response.text

        response = client.get(
            BROWSE,
            params={"view": "multipart", "file_type": "gcode"},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert [row["multipart"]["id"] for row in response.json()["items"]] == [
            group.id
        ]

    def test_preserves_cursor_after_rollback(
        self, client, auth_headers, make_model, db_session
    ):
        model = make_model("First")
        make_model("Second")
        first = client.get(BROWSE, params={"limit": 1}, headers=auth_headers)
        assert first.status_code == 200, first.text
        db_session.execute(
            text("UPDATE models SET name='Uncommitted' WHERE id=:id"), {"id": model.id}
        )
        db_session.rollback()

        response = client.get(
            BROWSE,
            params={"limit": 1, "cursor": first.json()["next_cursor"]},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text

    @pytest.mark.parametrize(
        "params",
        [{"view": "organized"}, {"limit": 0}, {"limit": 101}, {"cursor": "x" * 4097}],
        ids=["retired-view", "zero", "overflow", "oversized-cursor"],
    )
    def test_rejects_invalid_query_boundaries(self, client, auth_headers, params):
        response = client.get(BROWSE, params=params, headers=auth_headers)

        assert response.status_code == 422, response.text

    @pytest.mark.parametrize(
        "sort, names",
        [
            ("date-asc", ["Old", "Middle", "New"]),
            ("date-desc", ["New", "Middle", "Old"]),
        ],
        ids=["ascending", "descending"],
    )
    def test_globally_orders_dates(
        self, client, auth_headers, make_model, make_multipart_model, sort, names
    ):
        make_model("Old", updated_at=datetime(2025, 1, 1))
        make_multipart_model("Middle", updated_at=datetime(2025, 6, 1))
        make_model("New", updated_at=datetime(2026, 1, 1))

        response = client.get(BROWSE, params={"sort": sort}, headers=auth_headers)

        assert response.status_code == 200, response.text
        assert [row[row["kind"]]["name"] for row in response.json()["items"]] == names

    @pytest.mark.parametrize(
        "sort",
        ["success-desc", "printed-desc", "duration-asc", "filament-asc", "cost-asc"],
        ids=["success", "printed", "duration", "filament", "cost"],
    )
    def test_places_group_metric_nulls_last(
        self, client, auth_headers, make_model, make_multipart_model, sort, db_session
    ):
        from tests.factories import build_file, build_print_job

        model = make_model("Known duration")
        artifact = build_file(
            db_session,
            model,
            file_type=FileType.GCODE,
            metadata={"estimated_time_s": 100, "filament_weight_g": 10},
        )
        build_print_job(
            db_session,
            artifact,
            state=PrintJobState.COMPLETED,
            actual_duration_s=100,
            finished_at=TIED,
            cost=5,
        )
        group = make_multipart_model("Set")

        response = client.get(BROWSE, params={"sort": sort}, headers=auth_headers)

        assert response.status_code == 200, response.text
        assert [
            (row["kind"], row[row["kind"]]["id"]) for row in response.json()["items"]
        ] == [("model", model.id), ("multipart", group.id)]

    def test_pages_without_duplicates(
        self, client, auth_headers, make_model, make_multipart_model
    ):
        model = make_model("A")
        group = make_multipart_model("B")
        first = client.get(
            BROWSE, params={"sort": "name-asc", "limit": 1}, headers=auth_headers
        )
        assert first.status_code == 200, first.text

        second = client.get(
            BROWSE,
            params={
                "sort": "name-asc",
                "limit": 1,
                "cursor": first.json()["next_cursor"],
            },
            headers=auth_headers,
        )

        assert second.status_code == 200, second.text
        assert first.json()["items"][0]["model"]["id"] == model.id
        assert second.json()["items"][0]["multipart"]["id"] == group.id
        assert second.json()["next_cursor"] is None
        assert second.json()["total"] == 2

    def test_returns_empty_final_page(self, client, auth_headers):
        response = client.get(BROWSE, headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["items"] == []
        assert response.json()["total"] == 0
        assert response.json()["next_cursor"] is None

    @pytest.mark.parametrize(
        "direct, count", [(False, 2), (True, 1)], ids=["descendants", "direct"]
    )
    def test_filters_collection_before_pagination(
        self,
        client,
        auth_headers,
        make_collection,
        make_model,
        make_multipart_model,
        direct,
        count,
    ):
        folder = make_collection("Folder")
        child = make_collection("Child", parent=folder)
        make_model("Other")
        make_model("Child", collection=child)
        make_multipart_model("Folder", collection=folder)

        response = client.get(
            BROWSE,
            params={"collection": folder.path, "direct": direct, "limit": 1},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["total"] == count
        assert len(response.json()["items"]) == 1

    def test_filters_own_group_tags(
        self, client, auth_headers, make_model, make_multipart_model
    ):
        member = make_model()
        group = make_multipart_model()
        assert (
            client.patch(
                f"/api/v1/models/{member.id}",
                headers=auth_headers,
                json={"tags": ["member"]},
            ).status_code
            == 200
        )
        assert (
            client.put(
                f"/api/v1/multipart-models/{group.id}/parts",
                headers=auth_headers,
                json={"parts": [{"name": "Body", "model_ids": [member.id]}]},
            ).status_code
            == 200
        )
        assert (
            client.put(
                f"/api/v1/multipart-models/{group.id}/tags",
                headers=auth_headers,
                json={"tags": ["set"]},
            ).status_code
            == 200
        )

        response = client.get(
            BROWSE, params={"view": "multipart", "tag": "set"}, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert [row["multipart"]["id"] for row in response.json()["items"]] == [
            group.id
        ]
        assert (
            client.get(
                BROWSE,
                params={"view": "multipart", "tag": "member"},
                headers=auth_headers,
            ).json()["items"]
            == []
        )

    def test_filters_group_favorites(self, client, auth_headers, make_multipart_model):
        group = make_multipart_model()
        make_multipart_model()
        starred = client.put(
            f"/api/v1/multipart-models/{group.id}/star", headers=auth_headers
        )
        assert starred.status_code == 200, starred.text

        response = client.get(BROWSE, params={"favorites": True}, headers=auth_headers)

        assert response.status_code == 200, response.text
        assert [row["multipart"]["id"] for row in response.json()["items"]] == [
            group.id
        ]

    @pytest.mark.parametrize(
        "query",
        ["Dragon", "protective", "%_"],
        ids=["name", "description", "literal-wildcard"],
    )
    def test_filters_group_text(
        self, client, auth_headers, make_multipart_model, query
    ):
        group = make_multipart_model("Dragon %_", description="protective case")
        make_multipart_model("Other")

        response = client.get(
            BROWSE, params={"view": "multipart", "q": query}, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert [row["multipart"]["id"] for row in response.json()["items"]] == [
            group.id
        ]

    def test_excludes_unreadable_member_matches(
        self,
        client,
        auth_headers,
        make_user,
        headers_for,
        make_collection,
        make_model,
        make_file,
        make_multipart_model,
        grant_role,
    ):
        user = make_user()
        visible = make_collection("Visible")
        private = make_collection("Private")
        grant_role(user, visible, CollectionRole.VIEW)
        member = make_model(collection=private)
        make_file(member, file_type=FileType.GCODE)
        group = make_multipart_model(collection=visible)
        assert (
            client.put(
                f"/api/v1/multipart-models/{group.id}/parts",
                headers=auth_headers,
                json={"parts": [{"name": "Body", "model_ids": [member.id]}]},
            ).status_code
            == 200
        )

        response = client.get(
            BROWSE,
            params={"view": "multipart", "file_type": "gcode"},
            headers=headers_for(user),
        )

        assert response.status_code == 200, response.text
        assert response.json()["items"] == []

    def test_denies_printer_filters_to_members(self, client, make_user, headers_for):
        user = make_user()

        response = client.get(
            BROWSE, params={"printer_id": 1}, headers=headers_for(user)
        )

        assert response.status_code == 403, response.text

    def test_rejects_cursor_for_different_caller(
        self, client, auth_headers, make_user, headers_for, make_model
    ):
        user = make_user(superuser=True)
        make_model()
        make_model()
        first = client.get(BROWSE, params={"limit": 1}, headers=auth_headers)
        assert first.status_code == 200, first.text

        response = client.get(
            BROWSE,
            params={"limit": 1, "cursor": first.json()["next_cursor"]},
            headers=headers_for(user),
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "browse_cursor_invalid"

    def test_excludes_trashed_pinned_member_matches(
        self, client, auth_headers, make_model, make_multipart_model, db_session
    ):
        from tests.factories import build_file

        member = make_model()
        build_file(db_session, member, file_type=FileType.GCODE)
        pinned = build_file(db_session, member, file_type=FileType.STL, trashed=True)
        group = make_multipart_model()
        composed = client.put(
            f"/api/v1/multipart-models/{group.id}/parts",
            headers=auth_headers,
            json={"parts": [{"name": "Body", "model_ids": [member.id]}]},
        )
        assert composed.status_code == 200, composed.text
        db_session.execute(
            text(
                "UPDATE multipart_model_choices SET source_file_id=:file WHERE multipart_model_id=:group"
            ),
            {"file": pinned.id, "group": group.id},
        )
        db_session.commit()

        response = client.get(
            BROWSE,
            params={"view": "multipart", "file_type": "gcode"},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["items"] == []


class TestBrowseRevision:
    def test_returns_authoritative_revision(self, client, auth_headers, make_model):
        make_model()
        page = client.get(BROWSE, headers=auth_headers)
        assert page.status_code == 200, page.text

        response = client.get(BROWSE + "/revision", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json() == {
            "browse_revision": page.json()["browse_revision"],
            "authorization_revision": page.json()["authorization_revision"],
        }

    def test_requires_authentication(self, client):
        response = client.get(BROWSE + "/revision")

        assert response.status_code == 401, response.text

    def test_permits_restricted_reader(self, client, make_user, headers_for):
        user = make_user()

        response = client.get(BROWSE + "/revision", headers=headers_for(user))

        assert response.status_code == 200, response.text
        assert set(response.json()) == {"browse_revision", "authorization_revision"}


class TestBrowseAccessRevision:
    def test_detects_inherited_permission_revocation(
        self,
        client,
        headers_for,
        make_user,
        make_collection,
        make_model,
        grant_role,
        db_session,
    ):
        user = make_user()
        root = make_collection("Root")
        child = make_collection("Child", parent=root)
        make_model(collection=child)
        grant_role(user, root, CollectionRole.VIEW)
        before = client.get(BROWSE, headers=headers_for(user))
        assert before.status_code == 200, before.text
        assert before.json()["total"] == 1
        db_session.execute(
            text("DELETE FROM collection_permissions WHERE user_id=:id"),
            {"id": user.id},
        )
        db_session.commit()

        checked = client.get(BROWSE + "/revision", headers=headers_for(user))

        assert checked.status_code == 200, checked.text
        assert (
            checked.json()["authorization_revision"]
            != before.json()["authorization_revision"]
        )
        assert client.get(BROWSE, headers=headers_for(user)).json()["items"] == []

    @pytest.mark.parametrize(
        "statement",
        [
            "UPDATE users SET is_active=0 WHERE id=:id",
            "UPDATE users SET auth_version=auth_version+1 WHERE id=:id",
        ],
        ids=["account-disabled", "sessions-revoked"],
    )
    def test_rejects_revoked_caller(
        self, client, headers_for, make_user, db_session, statement
    ):
        user = make_user()
        headers = headers_for(user)
        db_session.execute(text(statement), {"id": user.id})
        db_session.commit()

        checked = client.get(BROWSE + "/revision", headers=headers)

        assert checked.status_code == 401, checked.text

    def test_keeps_access_revision_for_routine_metadata(
        self, client, auth_headers, make_model
    ):
        model = make_model()
        before = client.get(BROWSE + "/revision", headers=auth_headers).json()
        edited = client.patch(
            f"/api/v1/models/{model.id}",
            headers=auth_headers,
            json={"name": "New name"},
        )
        assert edited.status_code == 200, edited.text

        checked = client.get(BROWSE + "/revision", headers=auth_headers)

        assert checked.status_code == 200, checked.text
        assert checked.json()["browse_revision"] != before["browse_revision"]
        assert (
            checked.json()["authorization_revision"] == before["authorization_revision"]
        )

    def test_detects_collection_move(
        self, client, auth_headers, make_model, make_collection
    ):
        model = make_model()
        folder = make_collection("Private")
        before = client.get(BROWSE + "/revision", headers=auth_headers).json()
        edited = client.patch(
            f"/api/v1/models/{model.id}",
            headers=auth_headers,
            json={"collection": folder.path},
        )
        assert edited.status_code == 200, edited.text

        checked = client.get(BROWSE + "/revision", headers=auth_headers)

        assert (
            checked.json()["authorization_revision"] != before["authorization_revision"]
        )

    def test_rolls_back_access_revision(
        self, client, auth_headers, make_collection, db_session
    ):
        folder = make_collection("Folder")
        before = client.get(BROWSE + "/revision", headers=auth_headers).json()
        db_session.execute(
            text("UPDATE collections SET path='new-path' WHERE id=:id"),
            {"id": folder.id},
        )
        db_session.rollback()

        checked = client.get(BROWSE + "/revision", headers=auth_headers)

        assert checked.json() == before


class TestBrowseThumbnails:
    def test_deduplicates_and_preserves_requested_order(
        self, client, auth_headers, make_model
    ):
        first = make_model()
        second = make_model(thumbnail_path="123.png")
        response = client.get(
            BROWSE + "/thumbnails",
            params=[
                ("model_id", second.id),
                ("model_id", first.id),
                ("model_id", second.id),
            ],
            headers=auth_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["items"] == [
            {"model_id": second.id, "thumbnail_url": "/api/v1/files/123/thumbnail"},
            {"model_id": first.id, "thumbnail_url": None},
        ]
        authority = client.get(BROWSE + "/revision", headers=auth_headers).json()
        assert (
            response.json()["authorization_revision"]
            == authority["authorization_revision"]
        )

    @pytest.mark.parametrize("ids", [[], [0], [-1], list(range(1, 26))])
    def test_rejects_invalid_ids(self, client, auth_headers, ids):
        response = client.get(
            BROWSE + "/thumbnails",
            params=[("model_id", model_id) for model_id in ids],
            headers=auth_headers,
        )
        assert response.status_code == 422, response.text

    def test_requires_authentication(self, client):
        response = client.get(BROWSE + "/thumbnails", params={"model_id": 1})
        assert response.status_code == 401, response.text

    def test_omits_missing_trashed_and_unreadable_models(
        self, client, make_user, headers_for, make_collection, make_model, grant_role
    ):
        user = make_user()
        root = make_collection("Allowed")
        child = make_collection("Child", parent=root)
        hidden = make_collection("Hidden")
        trashed_folder = make_collection("Trashed", trashed=True)
        allowed = make_model(collection=child)
        private = make_model(collection=hidden)
        trashed = make_model(collection=child, trashed=True)
        in_trash = make_model(collection=trashed_folder)
        grant_role(user, root, CollectionRole.VIEW)
        response = client.get(
            BROWSE + "/thumbnails",
            params=[
                ("model_id", value)
                for value in [private.id, trashed.id, allowed.id, in_trash.id, 999999]
            ],
            headers=headers_for(user),
        )
        assert response.status_code == 200, response.text
        assert response.json()["items"] == [
            {"model_id": allowed.id, "thumbnail_url": None}
        ]

    def test_superuser_omits_models_in_trashed_folders(
        self, client, auth_headers, make_collection, make_model
    ):
        folder = make_collection("Trashed", trashed=True)
        model = make_model(collection=folder)
        response = client.get(
            BROWSE + "/thumbnails", params={"model_id": model.id}, headers=auth_headers
        )
        assert response.status_code == 200, response.text
        assert response.json()["items"] == []

    def test_returns_current_canonical_thumbnail_without_refreshing_page(
        self, client, auth_headers, make_model, make_file, db_session
    ):
        import hashlib

        model = make_model("Original")
        file = make_file(model)
        original = client.get(BROWSE, headers=auth_headers).json()
        path = f"{file.id}-recipe-two.png"
        db_session.execute(
            text(
                "UPDATE models SET thumbnail_file_id=:file, thumbnail_path=:path WHERE id=:id"
            ),
            {"file": file.id, "path": path, "id": model.id},
        )
        db_session.commit()
        response = client.get(
            BROWSE + "/thumbnails", params={"model_id": model.id}, headers=auth_headers
        )
        assert response.status_code == 200, response.text
        version = hashlib.sha256(path.encode()).hexdigest()[:12]
        assert response.json() == {
            "items": [
                {
                    "model_id": model.id,
                    "thumbnail_url": f"/api/v1/files/{file.id}/thumbnail?v={version}",
                }
            ],
            "authorization_revision": original["authorization_revision"],
        }
        assert original["items"][0]["model"]["thumbnail_url"] is None
        assert (
            client.get(BROWSE + "/revision", headers=auth_headers).json()[
                "browse_revision"
            ]
            != original["browse_revision"]
        )
