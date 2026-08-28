"""Moving one pending import through its states: edit, resolve, import, retry, dismiss.

Each of these routes schedules background work, and what they refuse is as load-bearing as
what they accept. Resolving is only for an item that has not been resolved yet — a review
item asked to resolve again would throw away the manifest the user is looking at.
Importing is gated on `review`, and the **selection is validated before anything is
scheduled**: a background task that discovers the ids are bogus fails somewhere the user
cannot see, so an invalid selection must be a 422 on the request that made it.

Retry is the interesting one: it decides for itself where the item goes back to. With no
manifest it returns to `captured` and re-resolves; with one it returns to `review` and
re-imports **only the files that failed**, because re-importing the ones that worked would
duplicate them.

Dismiss is terminal and must never take the imported model with it.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    BackgroundJob,
    File,
    FileType,
    InboxItem,
    InboxItemResult,
    InboxItemResultState,
    InboxItemState,
    Model,
)
from tests.factories import build_file, build_model


class TestUpdateItem:
    def test_renames_the_item(
        self, client: TestClient, make_user, headers_for, make_item
    ) -> None:
        owner = make_user("update-rename")
        row = make_item(owner)

        response = client.patch(
            f"/api/v1/inbox/{row.id}",
            headers=headers_for(owner),
            json={"title": "Renamed bracket"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["display_title"] == "Renamed bracket"

    def test_refuses_another_accounts_item(
        self, client: TestClient, make_user, make_item, user_headers
    ) -> None:
        row = make_item(make_user("update-owner"))

        response = client.patch(
            f"/api/v1/inbox/{row.id}",
            headers=user_headers("update-stranger"),
            json={"title": "Stolen"},
        )

        assert response.status_code == 404, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, make_user, make_item
    ) -> None:
        row = make_item(make_user("update-anon"))

        response = client.patch(f"/api/v1/inbox/{row.id}", json={"title": "Anonymous"})

        assert response.status_code == 401, response.text


class TestResolveItem:
    def test_schedules_a_failed_item_to_be_resolved_again(
        self, client: TestClient, make_user, headers_for, make_item, no_egress
    ) -> None:
        owner = make_user("resolve-failed")
        row = make_item(owner, state=InboxItemState.FAILED)

        response = client.post(
            f"/api/v1/inbox/{row.id}/resolve", headers=headers_for(owner)
        )

        assert response.status_code == 200, response.text
        assert no_egress == [row.id]

    def test_leaves_the_state_alone_until_the_background_work_runs(
        self, client: TestClient, make_user, headers_for, make_item, no_egress
    ) -> None:
        owner = make_user("resolve-state")
        row = make_item(owner, state=InboxItemState.FAILED)

        response = client.post(
            f"/api/v1/inbox/{row.id}/resolve", headers=headers_for(owner)
        )

        assert response.json()["state"] == "failed"

    def test_refuses_an_item_that_is_already_in_review(
        self, client: TestClient, make_user, headers_for, make_item
    ) -> None:
        owner = make_user("resolve-review")
        row = make_item(owner, state=InboxItemState.REVIEW)

        response = client.post(
            f"/api/v1/inbox/{row.id}/resolve", headers=headers_for(owner)
        )

        # Resolving again would discard the manifest the user is looking at.
        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "pending_import_not_resolvable"

    def test_refuses_another_accounts_item(
        self, client: TestClient, make_user, make_item, user_headers
    ) -> None:
        row = make_item(make_user("resolve-owner"))

        response = client.post(
            f"/api/v1/inbox/{row.id}/resolve", headers=user_headers("resolve-stranger")
        )

        assert response.status_code == 404, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, make_user, make_item
    ) -> None:
        row = make_item(make_user("resolve-anon"))

        assert client.post(f"/api/v1/inbox/{row.id}/resolve").status_code == 401


class TestImportItem:
    def test_schedules_the_import_of_the_selected_files(
        self, client: TestClient, make_user, headers_for, make_item, imports_run
    ) -> None:
        owner = make_user("import-schedules")
        row = make_item(
            owner,
            state=InboxItemState.REVIEW,
            manifest_json='{"kind": "direct", "title": "x"}',
        )

        response = client.post(
            f"/api/v1/inbox/{row.id}/import",
            headers=headers_for(owner),
            json={"selected_ids": ["a"]},
        )

        assert response.status_code == 200, response.text
        assert imports_run == [(row.id, ["a"])]

    def test_refuses_an_item_that_is_not_in_review(
        self, client: TestClient, make_user, headers_for, make_item
    ) -> None:
        owner = make_user("import-not-ready")
        row = make_item(owner, state=InboxItemState.CAPTURED)

        response = client.post(
            f"/api/v1/inbox/{row.id}/import",
            headers=headers_for(owner),
            json={"selected_ids": []},
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "pending_import_not_ready"

    @pytest.mark.parametrize(
        "requested", [["missing"], ["ok", "missing"]], ids=["all-bad", "one-bad"]
    )
    def test_refuses_a_selection_naming_a_file_the_manifest_does_not_have(
        self,
        client: TestClient,
        make_user,
        headers_for,
        make_item,
        imports_run,
        requested: list[str],
    ) -> None:
        owner = make_user(f"import-selection-{len(requested)}")
        row = make_item(
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=(
                '{"schema_version": 2, "kind": "model_files",'
                ' "files": [{"id": "ok", "name": "ok.stl"}]}'
            ),
        )

        response = client.post(
            f"/api/v1/inbox/{row.id}/import",
            headers=headers_for(owner),
            json={"selected_ids": requested},
        )

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "file_selection_invalid"

    def test_schedules_nothing_when_it_refuses_the_selection(
        self, client: TestClient, make_user, headers_for, make_item, imports_run
    ) -> None:
        owner = make_user("import-selection-unscheduled")
        row = make_item(
            owner,
            state=InboxItemState.REVIEW,
            manifest_json=(
                '{"schema_version": 2, "kind": "model_files",'
                ' "files": [{"id": "ok", "name": "ok.stl"}]}'
            ),
        )

        client.post(
            f"/api/v1/inbox/{row.id}/import",
            headers=headers_for(owner),
            json={"selected_ids": ["missing"]},
        )

        # A background task that discovers the ids are bogus fails out of sight.
        assert imports_run == []

    def test_refuses_another_accounts_item(
        self, client: TestClient, make_user, make_item, user_headers
    ) -> None:
        row = make_item(make_user("import-owner"), state=InboxItemState.REVIEW)

        response = client.post(
            f"/api/v1/inbox/{row.id}/import",
            headers=user_headers("import-stranger"),
            json={"selected_ids": []},
        )

        assert response.status_code == 404, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, make_user, make_item
    ) -> None:
        row = make_item(make_user("import-anon"))

        response = client.post(
            f"/api/v1/inbox/{row.id}/import", json={"selected_ids": []}
        )

        assert response.status_code == 401, response.text


class TestRetryItem:
    def test_returns_an_item_with_no_manifest_to_captured(
        self, client: TestClient, make_user, headers_for, make_item, no_egress
    ) -> None:
        owner = make_user("retry-captured")
        row = make_item(
            owner, state=InboxItemState.FAILED, retryable=True, manifest_json=""
        )

        response = client.post(
            f"/api/v1/inbox/{row.id}/retry", headers=headers_for(owner)
        )

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "captured"

    def test_schedules_a_fresh_resolve_for_an_item_with_no_manifest(
        self, client: TestClient, make_user, headers_for, make_item, no_egress
    ) -> None:
        owner = make_user("retry-resolves")
        row = make_item(
            owner, state=InboxItemState.FAILED, retryable=True, manifest_json=""
        )

        client.post(f"/api/v1/inbox/{row.id}/retry", headers=headers_for(owner))

        assert no_egress == [row.id]

    def test_reimports_only_the_files_that_failed(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
        make_item,
        imports_run,
    ) -> None:
        owner = make_user("retry-partial")
        row = make_item(
            owner,
            state=InboxItemState.COMPLETED,
            completion="partial",
            retryable=True,
            manifest_json='{"kind":"model_files","selected_ids":["bad"]}',
        )
        db_session.add(
            InboxItemResult(
                inbox_item_id=row.id,
                source_selection_id="bad",
                result_key="self",
                original_filename="bad.stl",
                state=InboxItemResultState.FAILED,
                error_code="captured_artifact_trashed",
                retryable=True,
            )
        )
        db_session.commit()

        response = client.post(
            f"/api/v1/inbox/{row.id}/retry", headers=headers_for(owner)
        )

        # Re-importing the files that worked would duplicate them.
        assert response.json()["state"] == "review"
        assert imports_run == [(row.id, ["bad"])]

    def test_refuses_a_retry_whose_stored_selection_no_longer_matches_the_manifest(
        self, client: TestClient, make_user, headers_for, make_item, imports_run
    ) -> None:
        owner = make_user("retry-selection-invalid")
        row = make_item(
            owner,
            state=InboxItemState.FAILED,
            retryable=True,
            manifest_json=(
                '{"schema_version": 2, "kind": "model_files", "selected_ids": ["bad"],'
                ' "files": [{"id": "ok", "name": "ok.stl"}]}'
            ),
        )

        response = client.post(
            f"/api/v1/inbox/{row.id}/retry", headers=headers_for(owner)
        )

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "file_selection_invalid"

    def test_schedules_nothing_when_it_refuses_the_stored_selection(
        self,
        client: TestClient,
        db_session: Session,
        make_user,
        headers_for,
        make_item,
        imports_run,
    ) -> None:
        owner = make_user("retry-selection-unscheduled")
        row = make_item(
            owner,
            state=InboxItemState.FAILED,
            retryable=True,
            manifest_json=(
                '{"schema_version": 2, "kind": "model_files", "selected_ids": ["bad"],'
                ' "files": [{"id": "ok", "name": "ok.stl"}]}'
            ),
        )
        jobs_before = db_session.exec(select(BackgroundJob)).all()

        client.post(f"/api/v1/inbox/{row.id}/retry", headers=headers_for(owner))

        assert imports_run == []
        assert db_session.exec(select(BackgroundJob)).all() == jobs_before

    def test_refuses_another_accounts_item(
        self, client: TestClient, make_user, make_item, user_headers
    ) -> None:
        row = make_item(make_user("retry-owner"))

        response = client.post(
            f"/api/v1/inbox/{row.id}/retry", headers=user_headers("retry-stranger")
        )

        assert response.status_code == 404, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, make_user, make_item
    ) -> None:
        row = make_item(make_user("retry-anon"))

        assert client.post(f"/api/v1/inbox/{row.id}/retry").status_code == 401


class TestDismissItem:
    def test_dismisses_the_item(
        self, client: TestClient, db_session: Session, make_user, headers_for, make_item
    ) -> None:
        owner = make_user("dismiss-plain")
        row = make_item(owner)

        response = client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        assert response.status_code == 204, response.text
        db_session.expire_all()
        assert db_session.get(InboxItem, row.id).state == InboxItemState.DISMISSED

    def test_keeps_the_model_a_completed_capture_already_imported(
        self, client: TestClient, db_session: Session, make_user, headers_for, make_item
    ) -> None:
        owner = make_user("dismiss-completed")
        model = build_model(
            db_session,
            name="Imported widget",
            slug="imported-widget",
            hash="d" * 64,
            source_url="https://makerworld.com/en/models/1234-widget",
        )
        artifact = build_file(
            db_session,
            model,
            path="imported/widget.stl",
            filename="widget.stl",
            file_type=FileType.STL,
            size_bytes=4,
            sha256="e" * 64,
        )
        job = BackgroundJob(
            id="completed-dismiss-job",
            owner_user_id=owner.id,
            state="completed",
            status_json='{"state":"completed"}',
            finished_at=utcnow(),
        )
        db_session.add(artifact)
        db_session.add(job)
        db_session.commit()
        db_session.refresh(artifact)
        row = make_item(
            owner,
            source_kind="BROWSER",
            state=InboxItemState.COMPLETED,
            background_job_id=job.id,
            resulting_model_id=model.id,
        )

        client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        # Dismissing the queue entry must never take the library row with it.
        db_session.expire_all()
        assert db_session.get(Model, model.id) is not None
        assert db_session.get(File, artifact.id) is not None

    def test_takes_the_dismissed_item_out_of_the_queue(
        self, client: TestClient, make_user, headers_for, make_item
    ) -> None:
        owner = make_user("dismiss-listing")
        row = make_item(owner)

        client.delete(f"/api/v1/inbox/{row.id}", headers=headers_for(owner))

        listed = client.get("/api/v1/inbox", headers=headers_for(owner))
        assert row.id not in {item["id"] for item in listed.json()}

    def test_refuses_another_accounts_item(
        self, client: TestClient, make_user, make_item, user_headers
    ) -> None:
        row = make_item(make_user("dismiss-owner"))

        response = client.delete(
            f"/api/v1/inbox/{row.id}", headers=user_headers("dismiss-stranger")
        )

        assert response.status_code == 404, response.text

    def test_rejects_an_unauthenticated_caller(
        self, client: TestClient, make_user, make_item
    ) -> None:
        row = make_item(make_user("dismiss-anon"))

        assert client.delete(f"/api/v1/inbox/{row.id}").status_code == 401
