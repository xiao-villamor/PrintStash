"""Applying one action to a set of pending imports at once.

A batch is a loop over the single-item paths, and the two things worth defending are what
happens at its edges. Ids are **de-duplicated**, so a UI that sends the same row twice
does not double-apply tags. And an item that cannot take the action is **dropped from the
response** rather than failing the batch — selecting twenty items and importing them must
not fail because one of them is still resolving.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import InboxItemState
from tests.factories import build_collection


class TestBatchItems:
    def test_moves_every_named_item_to_a_collection(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
        make_item,
    ) -> None:
        owner = make_user("batch-collection", superuser=True)
        row = make_item(owner)
        collection = build_collection(
            db_session, name="Batch target", slug="batch-target", path="batch-target"
        )

        response = client.post(
            "/api/v1/inbox/batch",
            headers=headers_for(owner, scope="admin"),
            json={
                "item_ids": [row.id],
                "action": "set_collection",
                "collection_id": collection.id,
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()[0]["target_collection_id"] == collection.id

    def test_adds_tags_without_dropping_the_ones_already_there(
        self, client: TestClient, make_user, headers_for, make_item
    ) -> None:
        owner = make_user("batch-tags")
        row = make_item(owner, requested_tags_json='["existing"]')

        response = client.post(
            "/api/v1/inbox/batch",
            headers=headers_for(owner),
            json={"item_ids": [row.id], "action": "add_tags", "tags": ["new"]},
        )

        assert set(response.json()[0]["requested_tags"]) == {"existing", "new"}

    def test_applies_an_action_once_to_a_repeated_id(
        self, client: TestClient, make_user, headers_for, make_item
    ) -> None:
        owner = make_user("batch-dedup")
        row = make_item(owner, requested_tags_json='["existing"]')

        response = client.post(
            "/api/v1/inbox/batch",
            headers=headers_for(owner),
            json={"item_ids": [row.id, row.id], "action": "add_tags", "tags": ["new"]},
        )

        assert len(response.json()) == 1

    def test_retries_every_named_item(
        self, client: TestClient, make_user, headers_for, make_item, no_egress
    ) -> None:
        owner = make_user("batch-retry")
        row = make_item(
            owner, state=InboxItemState.FAILED, retryable=True, manifest_json=""
        )

        response = client.post(
            "/api/v1/inbox/batch",
            headers=headers_for(owner),
            json={"item_ids": [row.id], "action": "retry"},
        )

        assert response.json()[0]["state"] == "captured"
        assert no_egress == [row.id]

    def test_imports_every_item_that_is_ready(
        self, client: TestClient, make_user, headers_for, make_item, imports_run
    ) -> None:
        owner = make_user("batch-import")
        row = make_item(
            owner,
            state=InboxItemState.REVIEW,
            manifest_json='{"kind": "direct", "title": "x"}',
        )

        response = client.post(
            "/api/v1/inbox/batch",
            headers=headers_for(owner),
            json={"item_ids": [row.id], "action": "import"},
        )

        assert response.status_code == 200, response.text
        assert imports_run == [(row.id, [])]

    def test_drops_an_item_that_is_not_ready_to_import(
        self, client: TestClient, make_user, headers_for, make_item, imports_run
    ) -> None:
        owner = make_user("batch-import-mixed")
        ready = make_item(
            owner,
            state=InboxItemState.REVIEW,
            manifest_json='{"kind": "direct", "title": "x"}',
        )
        not_ready = make_item(owner, state=InboxItemState.CAPTURED)

        response = client.post(
            "/api/v1/inbox/batch",
            headers=headers_for(owner),
            json={"item_ids": [ready.id, not_ready.id], "action": "import"},
        )

        # Twenty selected items must not fail because one is still resolving.
        assert {row["id"] for row in response.json()} == {ready.id}

    def test_dismisses_every_named_item(
        self, client: TestClient, make_user, headers_for, make_item
    ) -> None:
        owner = make_user("batch-dismiss")
        row = make_item(owner)

        response = client.post(
            "/api/v1/inbox/batch",
            headers=headers_for(owner),
            json={"item_ids": [row.id], "action": "dismiss"},
        )

        assert response.json()[0]["state"] == "dismissed"

    def test_refuses_a_batch_naming_another_accounts_item(
        self, client: TestClient, make_user, make_item, user_headers
    ) -> None:
        row = make_item(make_user("batch-owner"))

        response = client.post(
            "/api/v1/inbox/batch",
            headers=user_headers("batch-stranger"),
            json={"item_ids": [row.id], "action": "dismiss"},
        )

        assert response.status_code == 404, response.text

    def test_rejects_an_action_it_does_not_know(
        self, client: TestClient, make_user, headers_for, make_item
    ) -> None:
        owner = make_user("batch-unknown-action")
        row = make_item(owner)

        response = client.post(
            "/api/v1/inbox/batch",
            headers=headers_for(owner),
            json={"item_ids": [row.id], "action": "detonate"},
        )

        assert response.status_code == 422, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, make_user, make_item
    ) -> None:
        row = make_item(make_user("batch-anon"))

        response = client.post(
            "/api/v1/inbox/batch", json={"item_ids": [row.id], "action": "dismiss"}
        )

        assert response.status_code == 401, response.text
