"""Every visible entry is reachable without a global library-size cutoff (#335)."""

import pytest
from fastapi.testclient import TestClient

from app.core.time import utcnow
from app.db.models import CollectionRole, FileType
from tests.factories.protocols import MakeCollection, MakeModel

ENTRIES = "/api/v1/outliner/entries"


COLLECTIONS = "/api/v1/outliner/collections"
SEARCH = "/api/v1/outliner/search"
RESTORE = "/api/v1/outliner/restore"


def _read(client, headers, path=ENTRIES, **params):
    response = client.get(path, params=params, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


class TestOutliner:
    def test_reaches_a_model_beyond_the_former_global_limit(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        make_collection: MakeCollection,
        make_model: MakeModel,
    ) -> None:
        other = make_collection("Other")
        for index in range(501):
            make_model(f"Earlier {index:04}", collection=other)
        target = make_collection("Target")
        model = make_model("Zebra", collection=target)

        response = client.get(
            ENTRIES, params={"collection_id": target.id}, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()["items"]] == [model.id]

    @pytest.mark.parametrize(
        "kind", ["model", "multipart"], ids=["models", "multipart"]
    )
    @pytest.mark.parametrize("root", [False, True], ids=["folder", "root"])
    def test_pages_through_every_entry(
        self,
        client,
        auth_headers,
        make_model,
        make_multipart_model,
        make_collection,
        kind,
        root,
    ):
        folder = None if root else make_collection("Many")
        build = make_model if kind == "model" else make_multipart_model
        expected = [build(f"Entry {i:04}", collection=folder).id for i in range(503)]
        params = {} if root else {"collection_id": folder.id}
        found = []
        for _ in range(20):
            page = _read(client, auth_headers, limit=50, **params)
            found.extend(row["id"] for row in page["items"])
            if page["next_cursor"] is None:
                break
            params["cursor"] = page["next_cursor"]
        assert found == expected

    @pytest.mark.parametrize(
        "size", [0, 1, 49, 50, 51], ids=["empty", "one", "below", "exact", "above"]
    )
    def test_marks_the_last_page(self, client, auth_headers, make_model, size):
        for i in range(size):
            make_model(f"Entry {i}")
        page = _read(client, auth_headers)
        assert len(page["items"]) == min(size, 50)
        assert (page["next_cursor"] is not None) == (size > 50)

    def test_orders_equal_names_by_kind_then_id(
        self, client, auth_headers, make_model, make_multipart_model
    ):
        first = make_model("same")
        second = make_model("Same")
        group = make_multipart_model("SAME")
        found = []
        cursor = None
        for _ in range(3):
            page = _read(
                client, auth_headers, limit=1, **({"cursor": cursor} if cursor else {})
            )
            found.extend((row["kind"], row["id"]) for row in page["items"])
            cursor = page["next_cursor"]
        assert found == [
            ("model", first.id),
            ("model", second.id),
            ("multipart", group.id),
        ]
        assert cursor is None

    @pytest.mark.parametrize(
        "path", [ENTRIES, COLLECTIONS, SEARCH], ids=["entries", "collections", "search"]
    )
    def test_requires_authentication(self, client, path):
        assert (
            client.get(path, params={"q": "a"} if path == SEARCH else {}).status_code
            == 401
        )

    @pytest.mark.parametrize(
        "params",
        [{"limit": 0}, {"limit": 101}, {"collection_id": 0}],
        ids=["zero-limit", "large-limit", "zero-id"],
    )
    def test_rejects_invalid_parameters(self, client, auth_headers, params):
        assert (
            client.get(ENTRIES, params=params, headers=auth_headers).status_code == 422
        )

    @pytest.mark.parametrize(
        "cursor", ["no cursor", "e30=", "W10="], ids=["base64", "empty-object", "array"]
    )
    def test_rejects_malformed_cursor(self, client, auth_headers, cursor):
        assert (
            client.get(
                ENTRIES, params={"cursor": cursor}, headers=auth_headers
            ).status_code
            == 400
        )

    @pytest.mark.parametrize(
        "change",
        [{"view": "organized"}, {"favorites": True}, {"collection_id": 1}],
        ids=["view", "filters", "folder"],
    )
    def test_rejects_cursor_from_another_query(
        self, client, auth_headers, make_model, make_collection, change
    ):
        make_collection("Folder", id=1)
        make_model("A")
        make_model("B")
        first = _read(client, auth_headers, limit=1)
        response = client.get(
            ENTRIES,
            headers=auth_headers,
            params={"cursor": first["next_cursor"], **change},
        )
        assert response.status_code == 400

    def test_rejects_cursor_from_another_user(
        self, client, auth_headers, make_user, headers_for, make_model
    ):
        make_model("A")
        make_model("B")
        cursor = _read(client, auth_headers, limit=1)["next_cursor"]
        response = client.get(
            ENTRIES,
            headers=headers_for(make_user("other", superuser=True)),
            params={"cursor": cursor},
        )
        assert response.status_code == 400

    def test_hides_trashed_models(self, client, auth_headers, make_model):
        make_model("Gone", trashed=True)
        assert _read(client, auth_headers)["items"] == []

    def test_hides_entries_in_trashed_collections(
        self, client, auth_headers, make_collection, make_model, make_multipart_model
    ):
        folder = make_collection("Gone", deleted_at=utcnow())
        make_model("Hidden model", collection=folder)
        make_multipart_model("Hidden set", collection=folder)
        assert _read(client, auth_headers, SEARCH, q="Hidden")["items"] == []

    @pytest.mark.parametrize(
        "path,parameter",
        [(ENTRIES, "collection_id"), (COLLECTIONS, "parent_id")],
        ids=["entries", "collections"],
    )
    def test_hides_an_inaccessible_folder(
        self, client, make_collection, make_user, headers_for, path, parameter
    ):
        folder = make_collection("Private")
        headers = headers_for(make_user("outsider"))
        for identifier in [folder.id, 999999]:
            response = client.get(path, params={parameter: identifier}, headers=headers)
            assert response.status_code == 404

    def test_preserves_granted_roots(
        self, client, make_collection, make_user, headers_for, grant_role
    ):
        parent = make_collection("Private")
        child = make_collection("Granted", parent=parent)
        viewer = make_user("viewer")
        grant_role(viewer, child, CollectionRole.VIEW)
        page = _read(client, headers_for(viewer), COLLECTIONS)
        assert [(row["id"], row["display_path"]) for row in page["items"]] == [
            (child.id, "Granted")
        ]

    def test_counts_the_complete_branch(
        self, client, auth_headers, make_collection, make_model, make_multipart_model
    ):
        parent = make_collection("Parent")
        child = make_collection("Child", parent=parent)
        for i in range(51):
            make_model(f"Nested {i}", collection=child)
        make_model("Direct", collection=parent)
        make_model("Deleted", collection=parent, trashed=True)
        make_multipart_model("Group", collection=child)
        node = _read(client, auth_headers, COLLECTIONS)["items"][0]
        assert (
            node["direct_entry_count"],
            node["subtree_entry_count"],
            node["visible_child_count"],
        ) == (1, 53, 1)

    def test_keeps_a_filtered_descendant_branch(
        self, client, auth_headers, make_collection, make_model, make_tag, tag_model
    ):
        parent = make_collection("Parent")
        child = make_collection("Child", parent=parent)
        make_collection("Empty")
        tag_model(make_model("Hit", collection=child), make_tag("chosen"))
        make_model("Unfiltered sibling", collection=child)
        page = _read(client, auth_headers, COLLECTIONS, tag="chosen")
        assert page["items"][0]["model_count"] == 2
        assert [(row["id"], row["subtree_entry_count"]) for row in page["items"]] == [
            (parent.id, 1)
        ]

    def test_keeps_empty_folders_without_filters(
        self, client, auth_headers, make_collection
    ):
        empty = make_collection("Empty")
        assert _read(client, auth_headers, COLLECTIONS)["items"][0]["id"] == empty.id

    def test_reveals_a_folder_outside_the_page(
        self, client, auth_headers, make_collection
    ):
        first = make_collection("A")
        later = make_collection("Z")
        page = _read(client, auth_headers, COLLECTIONS, limit=1, reveal_id=later.id)
        assert [row["id"] for row in page["items"]] == [first.id]
        assert page["revealed"]["id"] == later.id

    @pytest.mark.parametrize(
        "view,expected",
        [
            ("all", ["model", "multipart"]),
            ("organized", ["multipart"]),
            ("components", ["model"]),
            ("multipart", ["multipart"]),
        ],
        ids=["all", "organized", "components", "multipart"],
    )
    def test_applies_the_view_before_pagination(
        self, client, auth_headers, make_model, make_multipart_model, view, expected
    ):
        model = make_model("A member")
        group = make_multipart_model("Z group")
        response = client.put(
            f"/api/v1/multipart-models/{group.id}",
            headers=auth_headers,
            json={"parts": [{"name": "Body", "choices": [{"model_id": model.id}]}]},
        )
        assert response.status_code == 200, response.text
        assert [
            row["kind"] for row in _read(client, auth_headers, view=view)["items"]
        ] == expected

    def test_does_not_hide_members_of_private_groups(
        self,
        client,
        auth_headers,
        make_collection,
        make_model,
        make_multipart_model,
        make_user,
        headers_for,
        grant_role,
    ):
        public = make_collection("Public")
        private = make_collection("Private")
        model = make_model("Member", collection=public)
        group = make_multipart_model("Private set", collection=private)
        response = client.put(
            f"/api/v1/multipart-models/{group.id}",
            headers=auth_headers,
            json={"parts": [{"name": "Body", "choices": [{"model_id": model.id}]}]},
        )
        assert response.status_code == 200, response.text
        viewer = make_user("member-viewer")
        grant_role(viewer, public, CollectionRole.VIEW)
        page = _read(
            client, headers_for(viewer), collection_id=public.id, view="organized"
        )
        assert [row["id"] for row in page["items"]] == [model.id]

    def test_search_reveals_a_grouped_model(
        self, client, auth_headers, make_model, make_multipart_model
    ):
        model = make_model("Needle")
        group = make_multipart_model("Group")
        response = client.put(
            f"/api/v1/multipart-models/{group.id}",
            headers=auth_headers,
            json={"parts": [{"name": "Body", "choices": [{"model_id": model.id}]}]},
        )
        assert response.status_code == 200
        page = _read(client, auth_headers, SEARCH, q="needle", view="organized")
        assert [row["id"] for row in page["items"]] == [model.id]

    @pytest.mark.parametrize(
        "needle", ["%", "_", "Á"], ids=["percent", "underscore", "accent"]
    )
    def test_searches_literal_names(
        self,
        client,
        auth_headers,
        make_model,
        make_collection,
        make_multipart_model,
        needle,
    ):
        folder = make_collection(f"Folder {needle}")
        make_model(f"Model {needle}", collection=folder)
        make_multipart_model(f"Group {needle}", collection=folder)
        make_model("Unrelated")
        items = _read(client, auth_headers, SEARCH, q=needle)["items"]
        assert len(items) == 3
        assert all(needle in row["name"] for row in items)
        assert all(row["collection_label"] == folder.name for row in items)

    @pytest.mark.parametrize(
        "params",
        [{}, {"q": " "}, {"q": "x", "collection_id": 1}],
        ids=["missing", "blank", "scoped"],
    )
    def test_rejects_invalid_search(self, client, auth_headers, params):
        assert (
            client.get(SEARCH, headers=auth_headers, params=params).status_code == 422
        )

    def test_restricts_printer_filters(self, client, make_user, headers_for):
        assert (
            client.get(
                ENTRIES,
                headers=headers_for(make_user("viewer")),
                params={"printer_presence": "any"},
            ).status_code
            == 403
        )

    def test_filters_by_artifact_type(
        self, client, auth_headers, make_model, make_file
    ):
        mesh = make_model("Mesh")
        make_file(mesh, file_type=FileType.STL)
        code = make_model("Code")
        make_file(code, file_type=FileType.GCODE)
        assert [
            row["id"] for row in _read(client, auth_headers, file_type="stl")["items"]
        ] == [mesh.id]

    def test_preserves_legacy_outliner_shape(self, client, auth_headers, make_model):
        model = make_model("Legacy")
        response = client.get("/api/v1/models/outliner", headers=auth_headers)
        assert response.status_code == 200
        assert isinstance(response.json(), list)
        assert response.json()[0]["id"] == model.id

    @pytest.mark.parametrize(
        "filters",
        [
            {"file_type": "gcode"},
            {"material_type": "PLA"},
            {"slicer_name": "PrusaSlicer"},
            {"printer_model": "MK4"},
            {"revision_status": "known_good"},
            {"storage": "external"},
            {"uploaded_after": "2025-01-01T00:00:00Z"},
            {"uploaded_before": "2027-01-01T00:00:00Z"},
        ],
        ids=[
            "type",
            "material",
            "slicer",
            "printer-model",
            "revision",
            "storage",
            "uploaded-after",
            "uploaded-before",
        ],
    )
    def test_applies_artifact_filter_families(
        self, client, auth_headers, make_model, make_file, filters
    ):
        from datetime import datetime, timezone

        from app.db.models import FileRevisionStatus

        chosen = make_model("Chosen")
        make_file(
            chosen,
            file_type=FileType.GCODE,
            status=FileRevisionStatus.KNOWN_GOOD,
            external=True,
            uploaded_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            metadata={
                "material_type": "PLA",
                "slicer_name": "PrusaSlicer",
                "printer_model": "MK4",
            },
        )
        make_model("No artifact")
        assert [
            row["id"] for row in _read(client, auth_headers, **filters)["items"]
        ] == [chosen.id]

    @pytest.mark.parametrize(
        "filters",
        [
            {"printed": True},
            {"print_outcome": "completed"},
            {"printed_after": "2025-01-01T00:00:00Z"},
            {"printed_before": "2027-01-01T00:00:00Z"},
            {"print_duration_min_s": 60},
            {"print_duration_max_s": 180},
        ],
        ids=["printed", "outcome", "after", "before", "min-duration", "max-duration"],
    )
    def test_applies_print_history_filter_families(
        self, client, auth_headers, make_model, make_file, db_session, filters
    ):
        from datetime import datetime, timezone

        from app.db.models import PrintJobState
        from tests.factories import build_print_job

        chosen = make_model("Printed")
        file = make_file(chosen, file_type=FileType.GCODE)
        build_print_job(
            db_session,
            file,
            state=PrintJobState.COMPLETED,
            finished_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            actual_duration_s=120,
        )
        make_model("Never printed")
        assert [
            row["id"] for row in _read(client, auth_headers, **filters)["items"]
        ] == [chosen.id]

    def test_keeps_multipart_filters_distinct(
        self,
        client,
        auth_headers,
        make_model,
        make_multipart_model,
        make_tag,
        db_session,
        make_user,
        headers_for,
    ):
        from tests.factories import build_multipart_model_star, tag_multipart_model

        group = make_multipart_model("Tagged favorite")
        make_multipart_model("Other")
        make_model("Not tagged")
        tag_multipart_model(db_session, group, make_tag("chosen"))
        user = make_user("star-owner", superuser=True)
        build_multipart_model_star(db_session, user, group)
        page = _read(
            client, headers_for(user), tag="chosen", favorites=True, material_type="PLA"
        )
        assert [(row["kind"], row["id"]) for row in page["items"]] == [
            ("multipart", group.id)
        ]

    def test_rejects_cursor_from_another_endpoint(
        self, client, auth_headers, make_model
    ):
        make_model("A")
        make_model("B")
        cursor = _read(client, auth_headers, limit=1)["next_cursor"]
        assert (
            client.get(
                SEARCH, params={"q": "A", "cursor": cursor}, headers=auth_headers
            ).status_code
            == 400
        )

    def test_search_returns_a_deep_uncached_location(
        self, client, auth_headers, make_collection, make_model
    ):
        parent = make_collection("Parent")
        child = make_collection("Never opened", parent=parent)
        model = make_model("Needle", collection=child)
        page = _read(client, auth_headers, SEARCH, q="Needle")
        assert [(r["id"], r["collection_label"]) for r in page["items"]] == [
            (model.id, "Parent/Never opened")
        ]

    @pytest.mark.parametrize("filter_kind", ["printer", "presence", "favorites"])
    def test_applies_user_specific_filters(
        self,
        client,
        db_session,
        make_user,
        headers_for,
        make_model,
        make_file,
        make_printer,
        filter_kind,
    ):
        from tests.factories import build_printer_file

        user = make_user("filter-owner", superuser=True)
        chosen = make_model("Chosen")
        make_model("Not chosen")
        file = make_file(chosen, file_type=FileType.GCODE)
        printer = make_printer()
        build_printer_file(db_session, printer, file=file)
        assert (
            client.put(
                f"/api/v1/models/{chosen.id}/star", headers=headers_for(user)
            ).status_code
            == 200
        )
        filters = {
            "printer": {"printer_id": printer.id},
            "presence": {"printer_presence": "any"},
            "favorites": {"favorites": True},
        }[filter_kind]
        assert [
            r["id"] for r in _read(client, headers_for(user), **filters)["items"]
        ] == [chosen.id]

    def test_uses_membership_beyond_500_multipart_sets(
        self, client, auth_headers, make_model, make_multipart_model
    ):
        for i in range(501):
            make_multipart_model(f"A set {i:04}")
        model = make_model("Part")
        group = make_multipart_model("Z final set")
        response = client.put(
            f"/api/v1/multipart-models/{group.id}",
            headers=auth_headers,
            json={"parts": [{"name": "Body", "choices": [{"model_id": model.id}]}]},
        )
        assert response.status_code == 200
        assert [
            r["id"] for r in _read(client, auth_headers, view="components")["items"]
        ] == [model.id]

    def test_pages_collections_without_duplicates(
        self, client, auth_headers, make_collection
    ):
        expected = [make_collection(f"Folder {i:03}").id for i in range(53)]
        first = _read(client, auth_headers, COLLECTIONS)
        last = _read(client, auth_headers, COLLECTIONS, cursor=first["next_cursor"])
        assert [r["id"] for r in first["items"] + last["items"]] == expected
        assert last["next_cursor"] is None


class TestOutlinerQueryAuthority:
    @pytest.mark.parametrize(
        "path", [ENTRIES, COLLECTIONS, SEARCH], ids=["entries", "collections", "search"]
    )
    def test_denies_printer_id_to_nonadministrator(
        self, client, make_user, headers_for, make_printer, path
    ):
        printer = make_printer()
        params = {"printer_id": printer.id}
        if path == SEARCH:
            params["q"] = "Needle"
        response = client.get(path, headers=headers_for(make_user()), params=params)
        assert response.status_code == 403
        assert response.json() == {"detail": "admin_required"}

    @pytest.mark.parametrize(
        "path", [ENTRIES, COLLECTIONS, SEARCH], ids=["entries", "collections", "search"]
    )
    @pytest.mark.parametrize(
        "selector", ["collection", "direct"], ids=["legacy-path", "legacy-direct"]
    )
    def test_rejects_legacy_collection_selectors(
        self, client, auth_headers, make_collection, path, selector
    ):
        folder = make_collection("Actual folder")
        params = {selector: folder.path if selector == "collection" else "true"}
        if path == SEARCH:
            params["q"] = "Needle"
        response = client.get(path, headers=auth_headers, params=params)
        assert response.status_code == 422
        assert response.json() == {"detail": "outliner_use_collection_id"}

    @pytest.mark.parametrize(
        "path", [ENTRIES, COLLECTIONS], ids=["entries", "collections"]
    )
    def test_rejects_search_text_on_listing_endpoints(self, client, auth_headers, path):
        response = client.get(path, headers=auth_headers, params={"q": "Needle"})
        assert response.status_code == 422
        assert response.json() == {"detail": "outliner_use_search"}

    @pytest.mark.parametrize(
        "selector", ["parent_id", "reveal_id"], ids=["parent", "reveal"]
    )
    def test_rejects_scoped_global_search(
        self, client, auth_headers, make_collection, selector
    ):
        folder = make_collection("Needle")
        response = client.get(
            SEARCH, headers=auth_headers, params={"q": "Needle", selector: folder.id}
        )
        assert response.status_code == 422
        assert response.json() == {"detail": "outliner_search_is_global"}

    def test_requires_parent_selector_for_collection_listing(
        self, client, auth_headers, make_collection
    ):
        folder = make_collection("Actual folder")
        response = client.get(
            COLLECTIONS, headers=auth_headers, params={"collection_id": folder.id}
        )
        assert response.status_code == 422
        assert response.json() == {"detail": "outliner_use_parent_id"}

    @pytest.mark.parametrize(
        "selector", ["parent_id", "reveal_id"], ids=["parent", "reveal"]
    )
    def test_requires_collection_selector_for_entry_listing(
        self, client, auth_headers, make_collection, selector
    ):
        folder = make_collection("Actual folder")
        response = client.get(
            ENTRIES, headers=auth_headers, params={selector: folder.id}
        )
        assert response.status_code == 422
        assert response.json() == {"detail": "outliner_use_collection_id"}


class TestOutlinerEditingBase:
    @pytest.mark.parametrize("kind", ["model", "multipart"])
    @pytest.mark.parametrize("root", [True, False])
    def test_returns_the_aggregate_editing_base(
        self,
        client,
        auth_headers,
        make_model,
        make_multipart_model,
        make_collection,
        kind,
        root,
    ):
        folder = None if root else make_collection("Versioned folder")
        build = make_model if kind == "model" else make_multipart_model
        row = build("Versioned leaf", collection=folder, edit_version=7)
        params = {} if root else {"collection_id": folder.id}
        items = _read(client, auth_headers, **params)["items"]
        assert len(items) == 1
        assert items[0]["kind"] == kind
        assert items[0]["id"] == row.id
        assert items[0]["name"] == row.name
        assert items[0]["collection_id"] == (folder.id if folder else None)
        assert items[0]["edit_version"] == 7

    def test_preserves_versions_across_mixed_continuation(
        self, client, auth_headers, make_model, make_multipart_model
    ):
        model = make_model("Same", edit_version=7)
        multipart = make_multipart_model("Same", edit_version=9, id=model.id)
        first = _read(client, auth_headers, limit=1)
        second = _read(client, auth_headers, limit=1, cursor=first["next_cursor"])
        assert [
            (row["kind"], row["id"], row["edit_version"])
            for row in first["items"] + second["items"]
        ] == [("model", model.id, 7), ("multipart", multipart.id, 9)]
        assert second["next_cursor"] is None

    def test_distinguishes_unversioned_collection_search_matches(
        self, client, auth_headers, make_model, make_multipart_model, make_collection
    ):
        folder = make_collection("Versioned folder")
        make_model("Versioned model", collection=folder, edit_version=7)
        make_multipart_model("Versioned set", collection=folder, edit_version=9)
        rows = {
            row["kind"]: row
            for row in _read(client, auth_headers, SEARCH, q="Versioned")["items"]
        }
        assert rows["model"]["edit_version"] == 7
        assert rows["multipart"]["edit_version"] == 9
        assert "edit_version" not in rows["collection"]
        assert rows["collection"]["id"] == folder.id

    def test_reads_the_version_acknowledged_by_another_editor(
        self, client, auth_headers, make_model, make_collection
    ):
        model = make_model("Original", edit_version=7)
        folder = make_collection("Destination")
        before = _read(client, auth_headers)["items"][0]
        response = client.patch(
            f"/api/v1/models/{model.id}",
            headers={
                **auth_headers,
                "If-Match": f'"model-{model.id}-e{model.edit_epoch}-v7"',
                "X-PrintStash-Edit-Contract": "conditional-v1",
            },
            json={"name": "Renamed", "collection": folder.path},
        )
        assert response.status_code == 200, response.text
        after = _read(client, auth_headers, collection_id=folder.id)["items"][0]
        assert before["edit_version"] == 7
        assert after["edit_version"] == response.json()["edit_version"] > 7
        assert after["name"] == "Renamed"
        assert after["collection"] == folder.path

    def test_returns_editing_versions_from_the_legacy_outliner(
        self, client, auth_headers, make_model
    ):
        model = make_model("Legacy projection", edit_version=13)
        response = client.get("/api/v1/models/outliner", headers=auth_headers)
        assert response.status_code == 200, response.text
        assert [(row["id"], row["edit_version"]) for row in response.json()] == [
            (model.id, 13)
        ]

    def test_requires_aggregate_versions_in_the_public_read_schema(self, client):
        schemas = client.get("/openapi.json").json()["components"]["schemas"]
        for name in ("OutlinerModelRead", "OutlinerModel", "OutlinerMultipart"):
            assert "edit_version" in schemas[name]["required"]
            assert schemas[name]["properties"]["edit_version"]["exclusiveMinimum"] == 0
        assert "edit_version" not in schemas["OutlinerCollectionMatch"]["properties"]


class TestRestore:
    @pytest.mark.parametrize(
        "with_folder", [False, True], ids=["unfiled-only", "with-folder"]
    )
    def test_restores_unfiled_page(
        self, client, auth_headers, make_model, make_collection, with_folder
    ):
        model = make_model("Unfiled")
        if with_folder:
            make_collection("Folder")

        response = client.post(RESTORE, headers=auth_headers, json={})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["collections"][0]["page"]["parent_direct_entry_count"] == 1
        assert [row["id"] for row in body["entries"][0]["page"]["items"]] == [model.id]

    def test_restores_deep_expanded_branches_in_one_bounded_response(
        self, client, auth_headers, make_collection, make_model
    ):
        root = make_collection("Root")
        child = make_collection("Child", parent=root)
        grandchild = make_collection("Grandchild", parent=child)
        model = make_model("Leaf", collection=grandchild)

        response = client.post(
            RESTORE,
            headers=auth_headers,
            json={
                "expanded_paths": [root.path, child.path, grandchild.path],
            },
        )

        assert response.status_code == 200, response.text
        pages = {
            row["parent_id"]: row["page"] for row in response.json()["collections"]
        }
        assert [row["id"] for row in pages[None]["items"]] == [root.id]
        assert [row["id"] for row in pages[root.id]["items"]] == [child.id]
        assert [row["id"] for row in pages[child.id]["items"]] == [grandchild.id]
        assert pages[None]["items"][0]["subtree_entry_count"] == 1
        leaves = {
            row["collection_id"]: row["page"] for row in response.json()["entries"]
        }
        assert [row["id"] for row in leaves[grandchild.id]["items"]] == [model.id]
        assert pages[grandchild.id]["parent_direct_entry_count"] == 1
        assert leaves[grandchild.id]["items"][0]["name"] == model.name
        assert leaves[grandchild.id]["items"][0]["edit_epoch"] == model.edit_epoch
        assert leaves[grandchild.id]["items"][0]["edit_version"] == model.edit_version

    def test_restores_empty_expanded_branches_without_filters(
        self, client, auth_headers, make_collection
    ):
        root = make_collection("Empty root")
        child = make_collection("Empty child", parent=root)
        response = client.post(
            RESTORE,
            headers=auth_headers,
            json={"expanded_paths": [root.path, child.path]},
        )
        assert response.status_code == 200, response.text
        pages = {
            row["parent_id"]: row["page"] for row in response.json()["collections"]
        }
        assert [row["id"] for row in pages[None]["items"]] == [root.id]
        assert [row["id"] for row in pages[root.id]["items"]] == [child.id]
        assert pages[child.id]["items"] == []
        assert all(page["parent_direct_entry_count"] == 0 for page in pages.values())

    def test_returns_the_restored_parent_label_for_entries(
        self, client, auth_headers, make_collection, make_model
    ):
        root = make_collection("Parts")
        child = make_collection("Brackets", parent=root)
        make_model("Bracket", collection=child)

        response = client.post(
            RESTORE,
            headers=auth_headers,
            json={"expanded_paths": [root.path, child.path]},
        )

        assert response.status_code == 200, response.text
        entries = next(
            level["page"]["items"]
            for level in response.json()["entries"]
            if level["collection_id"] == child.id
        )
        assert entries[0]["collection_label"] == "Parts/Brackets"

    def test_continues_restored_folder_pages_with_their_original_cursor(
        self, client, auth_headers, make_collection
    ):
        parent = make_collection("Parent")
        first = make_collection("A", parent=parent)
        second = make_collection("B", parent=parent)

        restored = client.post(
            RESTORE,
            headers=auth_headers,
            json={
                "expanded_paths": [parent.path],
                "limit": 1,
            },
        ).json()
        page = next(
            row["page"]
            for row in restored["collections"]
            if row["parent_id"] == parent.id
        )
        continuation = _read(
            client,
            auth_headers,
            COLLECTIONS,
            parent_id=parent.id,
            cursor=page["next_cursor"],
            limit=1,
        )

        assert [row["id"] for row in page["items"] + continuation["items"]] == [
            first.id,
            second.id,
        ]
        assert continuation["next_cursor"] is None

    def test_continues_restored_entry_pages_with_their_original_cursor(
        self, client, auth_headers, make_collection, make_model
    ):
        parent = make_collection("Parent")
        first = make_model("A", collection=parent)
        second = make_model("B", collection=parent)

        restored = client.post(
            RESTORE,
            headers=auth_headers,
            json={
                "expanded_paths": [parent.path],
                "limit": 1,
            },
        ).json()
        page = next(
            row["page"]
            for row in restored["entries"]
            if row["collection_id"] == parent.id
        )
        continuation = _read(
            client,
            auth_headers,
            collection_id=parent.id,
            cursor=page["next_cursor"],
            limit=1,
        )

        assert [row["id"] for row in page["items"] + continuation["items"]] == [
            first.id,
            second.id,
        ]
        assert continuation["next_cursor"] is None

    def test_preserves_granted_roots_during_restoration(
        self,
        client,
        auth_headers,
        make_collection,
        make_model,
        make_user,
        headers_for,
        grant_role,
    ):
        parent = make_collection("Private")
        granted = make_collection("Granted", parent=parent)
        model = make_model("Visible", collection=granted)
        make_model("Private unfiled model")
        viewer = make_user("restore-viewer")
        grant_role(viewer, granted, CollectionRole.VIEW)

        administrator = client.post(
            RESTORE,
            headers=auth_headers,
            json={"view": "all", "expanded_paths": [parent.path, granted.path]},
        )
        assert administrator.status_code == 200, administrator.text
        assert (
            administrator.json()["collections"][0]["page"]["parent_direct_entry_count"]
            == 1
        )

        response = client.post(
            RESTORE,
            headers=headers_for(viewer),
            json={
                "view": "all",
                "expanded_paths": [granted.path],
                "selected_path": granted.path,
            },
        )

        assert response.status_code == 200, response.text
        root = response.json()["collections"][0]["page"]
        assert [(row["id"], row["display_path"]) for row in root["items"]] == [
            (granted.id, "Granted")
        ]
        assert root["revealed"]["id"] == granted.id
        assert root["parent_direct_entry_count"] == 0
        assert response.json()["entries"][0]["page"]["items"] == []
        assert response.json()["entries"][1]["page"]["items"][0]["id"] == model.id
        assert "Private" not in response.text

    def test_ignores_inaccessible_persisted_paths(
        self, client, make_collection, make_model, make_user, headers_for
    ):
        private = make_collection("Secret")
        make_model("Secret model", collection=private)
        viewer = make_user("outsider")

        response = client.post(
            RESTORE,
            headers=headers_for(viewer),
            json={
                "expanded_paths": [private.path],
                "selected_path": private.path,
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["collections"] == [
            {
                "parent_id": None,
                "page": {
                    "items": [],
                    "next_cursor": None,
                    "parent_direct_entry_count": 0,
                    "revealed": None,
                },
            }
        ]
        assert response.json()["entries"] == [
            {
                "collection_id": None,
                "page": {
                    "items": [],
                    "next_cursor": None,
                },
            }
        ]
        assert "Secret" not in response.text

    def test_ignores_trashed_persisted_paths(
        self, client, auth_headers, make_collection, make_model
    ):
        trashed = make_collection("Gone", deleted_at=utcnow())
        make_model("Gone model", collection=trashed)

        response = client.post(
            RESTORE, headers=auth_headers, json={"expanded_paths": [trashed.path]}
        )

        assert response.status_code == 200, response.text
        assert response.json()["collections"][0]["page"]["items"] == []
        assert response.json()["entries"][0]["page"]["items"] == []
        assert len(response.json()["collections"]) == 1

    def test_ignores_removed_persisted_paths(
        self, client, auth_headers, make_collection
    ):
        root = make_collection("Existing")

        response = client.post(
            RESTORE, headers=auth_headers, json={"expanded_paths": ["removed"]}
        )

        assert response.status_code == 200, response.text
        assert [
            row["id"] for row in response.json()["collections"][0]["page"]["items"]
        ] == [root.id]
        assert len(response.json()["collections"]) == 1

    def test_filters_restored_branches_before_paging(
        self, client, auth_headers, make_collection, make_model, make_tag, tag_model
    ):
        root = make_collection("Root")
        child = make_collection("Child", parent=root)
        chosen = make_model("Chosen", collection=child)
        tag_model(chosen, make_tag("chosen"))
        make_model("Other", collection=child)
        make_collection("Empty", parent=root)

        response = client.post(
            RESTORE,
            headers=auth_headers,
            json={
                "expanded_paths": [root.path, child.path],
                "tag": ["chosen"],
            },
        )

        assert response.status_code == 200, response.text
        pages = {
            row["parent_id"]: row["page"] for row in response.json()["collections"]
        }
        assert [
            (row["id"], row["subtree_entry_count"]) for row in pages[root.id]["items"]
        ] == [(child.id, 1)]
        assert pages[root.id]["items"][0]["model_count"] == 2
        assert [
            row["id"] for row in response.json()["entries"][2]["page"]["items"]
        ] == [chosen.id]

    @pytest.mark.parametrize(
        "view,expected",
        [
            ("all", ["model", "multipart"]),
            ("organized", ["multipart"]),
            ("components", ["model"]),
            ("multipart", ["multipart"]),
        ],
        ids=["all", "organized", "components", "multipart"],
    )
    def test_applies_the_library_view_to_restored_entries(
        self,
        client,
        auth_headers,
        make_collection,
        make_model,
        make_multipart_model,
        view,
        expected,
    ):
        root = make_collection("Root")
        model = make_model("A member", collection=root)
        group = make_multipart_model("Z group", collection=root)
        assigned = client.put(
            f"/api/v1/multipart-models/{group.id}",
            headers=auth_headers,
            json={"parts": [{"name": "Body", "choices": [{"model_id": model.id}]}]},
        )
        assert assigned.status_code == 200, assigned.text

        response = client.post(
            RESTORE,
            headers=auth_headers,
            json={
                "expanded_paths": [root.path],
                "view": view,
            },
        )

        assert response.status_code == 200, response.text
        assert [
            row["kind"] for row in response.json()["entries"][1]["page"]["items"]
        ] == expected
        assert response.json()["collections"][0]["page"]["items"][0]["model_count"] == 1

    def test_reveals_the_selected_folder_outside_a_restored_page(
        self, client, auth_headers, make_collection
    ):
        first = make_collection("A")
        selected = make_collection("Z")

        response = client.post(
            RESTORE,
            headers=auth_headers,
            json={
                "expanded_paths": [],
                "selected_path": selected.path,
                "limit": 1,
            },
        )

        assert response.status_code == 200, response.text
        page = response.json()["collections"][0]["page"]
        assert [row["id"] for row in page["items"]] == [first.id]
        assert page["revealed"]["id"] == selected.id
        assert (
            _read(
                client, auth_headers, COLLECTIONS, cursor=page["next_cursor"], limit=1
            )["items"][0]["id"]
            == selected.id
        )

    def test_requires_authentication_for_restoration(self, client):
        response = client.post(RESTORE, json={"expanded_paths": []})

        assert response.status_code == 401, response.text

    @pytest.mark.parametrize(
        "body",
        [
            {"expanded_paths": ["path"] * 17},
            {"expanded_paths": [""]},
            {"expanded_paths": ["p" * 513]},
            {"limit": 0},
            {"limit": 101},
            {"selected_path": ""},
        ],
        ids=[
            "too-many-paths",
            "empty-path",
            "long-path",
            "zero-limit",
            "large-limit",
            "empty-selection",
        ],
    )
    def test_validates_restoration_bounds(self, client, auth_headers, body):
        response = client.post(RESTORE, headers=auth_headers, json=body)

        assert response.status_code == 422, response.text

    @pytest.mark.parametrize(
        "body",
        [
            {"cursor": "cursor"},
            {"parent_id": 1},
            {"collection_id": 1},
            {"reveal_id": 1},
            {"direct": True},
            {"q": "search"},
            {"collection": "folder"},
        ],
        ids=[
            "cursor",
            "parent",
            "collection-id",
            "reveal",
            "direct",
            "search",
            "collection-path",
        ],
    )
    def test_rejects_incompatible_restoration_scope(self, client, auth_headers, body):
        response = client.post(RESTORE, headers=auth_headers, json=body)

        assert response.status_code == 422, response.text

    def test_restricts_printer_filters_during_restoration(
        self, client, make_user, headers_for
    ):
        viewer = make_user("restore-printer-viewer")

        response = client.post(
            RESTORE, headers=headers_for(viewer), json={"printer_presence": "any"}
        )

        assert response.status_code == 403, response.text
