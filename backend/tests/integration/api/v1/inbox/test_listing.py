"""Reading the pending-import queue.

The queue is owner-scoped and the scoping is the whole point: an inbox row carries a URL
somebody visited, so another ordinary user must not see it, and asking for one by id must
answer 404 rather than 403 — a 403 would confirm the row exists. A superuser may inspect
every queue, which is what makes the app supportable.

A completed item keeps its per-file results so a partial import can say which file failed
and whether retrying it is worth anything.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import InboxItemResult, InboxItemResultState, InboxItemState
from tests.factories import build_file, build_model


class TestListItems:
    def test_lists_the_callers_own_items(
        self, client: TestClient, make_user, headers_for, make_item
    ) -> None:
        owner = make_user("list-owner")
        row = make_item(owner)

        body = client.get("/api/v1/inbox", headers=headers_for(owner)).json()

        assert [item["id"] for item in body] == [row.id]

    def test_hides_another_accounts_items_from_an_ordinary_user(
        self, client: TestClient, make_user, make_item, user_headers
    ) -> None:
        make_item(make_user("list-hidden-owner"))

        body = client.get("/api/v1/inbox", headers=user_headers("list-stranger")).json()

        assert body == []

    def test_shows_every_queue_to_a_superuser(
        self, client: TestClient, make_user, make_item, user_headers
    ) -> None:
        row = make_item(make_user("list-admin-owner"))

        body = client.get(
            "/api/v1/inbox",
            headers=user_headers("list-admin", is_superuser=True, scope="admin"),
        ).json()

        assert row.id in {item["id"] for item in body}

    def test_can_leave_out_the_items_that_already_finished(
        self, client: TestClient, make_user, headers_for, make_item
    ) -> None:
        owner = make_user("list-completed")
        pending = make_item(owner)
        make_item(owner, state=InboxItemState.COMPLETED)

        body = client.get(
            "/api/v1/inbox?include_completed=false", headers=headers_for(owner)
        ).json()

        assert [item["id"] for item in body] == [pending.id]

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/inbox").status_code == 401


class TestGetItem:
    def test_returns_the_callers_own_item(
        self, client: TestClient, make_user, headers_for, make_item
    ) -> None:
        owner = make_user("get-owner")
        row = make_item(owner)

        response = client.get(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        assert response.status_code == 200, response.text
        assert response.json()["id"] == row.id

    def test_includes_the_per_file_results_of_an_import(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
        make_item,
    ) -> None:
        owner = make_user("get-results")
        row = make_item(owner)
        # Real rows: `inbox_item_results.model_id` and `.file_id` are foreign keys,
        # so the ids an import records have to be the ones it actually produced.
        imported = build_model(db_session, name="Imported", slug="imported")
        imported_file = build_file(db_session, imported, filename="bracket.stl")
        db_session.add(
            InboxItemResult(
                inbox_item_id=row.id,
                source_selection_id="remote-stl",
                result_key="self",
                original_filename="bracket.stl",
                state=InboxItemResultState.IMPORTED,
                model_id=imported.id,
                file_id=imported_file.id,
                retryable=False,
            )
        )
        db_session.commit()

        response = client.get(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        assert response.json()["results"][0]["state"] == "imported"
        assert response.json()["results"][0]["result_key"] == "self"

    def test_hides_another_accounts_item_behind_a_404(
        self, client: TestClient, make_user, make_item, user_headers
    ) -> None:
        row = make_item(make_user("get-hidden-owner"))

        response = client.get(
            f"/api/v1/inbox/{row.id}", headers=user_headers("get-stranger")
        )

        # 403 would confirm the row exists, and the row is a URL somebody visited.
        assert response.status_code == 404, response.text

    def test_reports_an_item_that_does_not_exist(
        self, client: TestClient, user_headers
    ) -> None:
        response = client.get("/api/v1/inbox/9999", headers=user_headers("get-missing"))

        assert response.status_code == 404, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, make_user, make_item
    ) -> None:
        row = make_item(make_user("get-anon-owner"))

        assert client.get(f"/api/v1/inbox/{row.id}").status_code == 401
