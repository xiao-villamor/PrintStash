"""Defends resolve item success schedules background resolve at the services inbox integration boundary.

A regression could cross an owner boundary or lose a captured import during a retry.
"""

from __future__ import annotations

from ._inbox_api_shared import (
    BackgroundJob,
    BackgroundTasks,
    Collection,
    File,
    FileType,
    HTTPException,
    InboxImportRequest,
    InboxItem,
    InboxItemResult,
    InboxItemResultState,
    InboxItemState,
    Model,
    Session,
    TestClient,
    _BackgroundTaskRecorder,
    _capture_source,
    _headers,
    _make_item,
    _user,
    cast,
    create_access_token,
    get_session_factory,
    inbox,
    inbox_api,
    json,
    pytest,
    select,
    utcnow,
)


def test_resolve_item_success_schedules_background_resolve(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "resolve-success", admin=True)
    owner = _user(db_session, "resolve-success-owner")
    row = _make_item(db_session, owner, state=InboxItemState.FAILED)
    calls: list[int] = []

    async def fake_resolve(item_id: int) -> None:
        calls.append(item_id)

    monkeypatch.setattr(inbox, "resolve", fake_resolve)

    response = client.post(f"/api/v1/inbox/{row.id}/resolve", headers=headers)

    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert calls == [row.id]


def test_import_item_requires_review_state(
    client: TestClient, db_session: Session
) -> None:
    headers = _headers(db_session, "import-not-ready", admin=True)
    owner = _user(db_session, "import-not-ready-owner")
    row = _make_item(db_session, owner, state=InboxItemState.CAPTURED)

    response = client.post(
        f"/api/v1/inbox/{row.id}/import", headers=headers, json={"selected_ids": []}
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "pending_import_not_ready"


def test_import_item_success_schedules_run_import(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "import-success", admin=True)
    owner = _user(db_session, "import-success-owner")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json='{"kind": "direct", "title": "x"}',
    )
    calls: list[tuple[int, list[str]]] = []

    async def fake_run_import(
        item_id: int, selected_ids: list[str], _session_factory
    ) -> None:
        calls.append((item_id, selected_ids))

    monkeypatch.setattr(inbox, "run_import", fake_run_import)

    response = client.post(
        f"/api/v1/inbox/{row.id}/import",
        headers=headers,
        json={"selected_ids": ["a"]},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "review"
    assert calls == [(row.id, ["a"])]


@pytest.mark.parametrize("requested", [["missing"], ["ok", "missing"]])
def test_import_route_rejects_invalid_v2_selection_before_scheduling(
    db_session: Session, requested: list[str]
) -> None:
    owner = _user(db_session, f"import-selection-route-{len(requested)}")
    row = _make_item(
        db_session,
        owner,
        source_url="https://makerworld.com/en/models/1234-widget",
        source_hostname="makerworld.com",
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps(
            {
                "schema_version": 2,
                "kind": "model_files",
                "source": _capture_source(),
                "files": [
                    {"id": "ok", "name": "ok.stl", "file_type": "stl", "size": 1}
                ],
                "selected_ids": ["ok"],
            }
        ),
    )
    assert row.id is not None
    background = _BackgroundTaskRecorder()
    jobs_before = db_session.exec(select(BackgroundJob)).all()

    with pytest.raises(HTTPException) as exc_info:
        inbox_api.import_item(
            row.id,
            InboxImportRequest(selected_ids=requested),
            cast(BackgroundTasks, background),
            current_user=owner,
            session=db_session,
            session_factory=get_session_factory(),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "file_selection_invalid"
    assert background.tasks == []
    assert db_session.exec(select(BackgroundJob)).all() == jobs_before


def test_retry_item_schedules_resolve_when_returned_to_captured(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "retry-success", admin=True)
    owner = _user(db_session, "retry-success-owner")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.FAILED,
        retryable=True,
        manifest_json="",
    )
    calls: list[int] = []

    async def fake_resolve(item_id: int) -> None:
        calls.append(item_id)

    monkeypatch.setattr(inbox, "resolve", fake_resolve)

    response = client.post(f"/api/v1/inbox/{row.id}/retry", headers=headers)

    assert response.status_code == 200
    assert response.json()["state"] == "captured"
    assert calls == [row.id]


def test_retry_partial_schedules_failed_selection_only(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    owner = _user(db_session, "retry-partial-api")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.COMPLETED,
        completion="partial",
        retryable=True,
        manifest_json='{"kind":"model_files","selected_ids":["bad"]}',
    )
    result = InboxItemResult(
        inbox_item_id=row.id,
        source_selection_id="bad",
        result_key="self",
        original_filename="bad.stl",
        state=InboxItemResultState.FAILED,
        error_code="captured_artifact_trashed",
        retryable=True,
    )
    db_session.add(result)
    db_session.commit()
    calls: list[tuple[int, list[str]]] = []

    async def fake_run_import(item_id: int, selected_ids: list[str], _factory) -> None:
        calls.append((item_id, selected_ids))

    monkeypatch.setattr(inbox, "run_import", fake_run_import)
    headers = {
        "Authorization": f"Bearer {create_access_token(owner.id, owner.username, scope='write')}"
    }

    response = client.post(f"/api/v1/inbox/{row.id}/retry", headers=headers)

    assert response.status_code == 200
    assert response.json()["state"] == "review"
    assert calls == [(row.id, ["bad"])]


def test_retry_route_rejects_invalid_v2_selection_before_scheduling(
    db_session: Session,
) -> None:
    owner = _user(db_session, "retry-selection-route")
    row = _make_item(
        db_session,
        owner,
        source_url="https://makerworld.com/en/models/1234-widget",
        source_hostname="makerworld.com",
        state=InboxItemState.FAILED,
        retryable=True,
        manifest_json=json.dumps(
            {
                "schema_version": 2,
                "kind": "model_files",
                "source": _capture_source(),
                "files": [
                    {"id": "ok", "name": "ok.stl", "file_type": "stl", "size": 1}
                ],
                "selected_ids": ["missing"],
            }
        ),
    )
    assert row.id is not None
    background = _BackgroundTaskRecorder()
    jobs_before = db_session.exec(select(BackgroundJob)).all()

    with pytest.raises(HTTPException) as exc_info:
        inbox_api.retry_item(
            row.id,
            cast(BackgroundTasks, background),
            current_user=owner,
            session=db_session,
            session_factory=get_session_factory(),
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "file_selection_invalid"
    assert background.tasks == []
    assert db_session.exec(select(BackgroundJob)).all() == jobs_before


def test_dismiss_item_returns_204(client: TestClient, db_session: Session) -> None:
    headers = _headers(db_session, "dismiss-owner", admin=True)
    owner = _user(db_session, "dismiss-owner-user")
    row = _make_item(db_session, owner)

    response = client.delete(f"/api/v1/inbox/{row.id}", headers=headers)

    assert response.status_code == 204
    db_session.expire_all()
    refreshed = db_session.get(InboxItem, row.id)
    assert refreshed.state == InboxItemState.DISMISSED


def test_dismiss_completed_capture_after_terminal_cleanup_preserves_model(
    client: TestClient, db_session: Session
) -> None:
    """A completed capture has no review lease left to return before dismissing."""
    owner = _user(db_session, "dismiss-completed-owner")
    model = Model(
        name="Imported widget",
        slug="imported-widget",
        hash="d" * 64,
        source_url="https://makerworld.com/en/models/1234-widget",
    )
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    artifact = File(
        model_id=model.id,
        path="imported/widget.stl",
        original_filename="widget.stl",
        file_type=FileType.STL,
        version=1,
        size_bytes=4,
        sha256="e" * 64,
    )
    db_session.add(artifact)
    db_session.commit()
    db_session.refresh(artifact)
    job = BackgroundJob(
        id="completed-dismiss-job",
        owner_user_id=owner.id,
        state="completed",
        status_json='{"state":"completed"}',
        finished_at=utcnow(),
    )
    db_session.add(job)
    db_session.commit()
    row = _make_item(
        db_session,
        owner,
        source_kind="BROWSER",
        state=InboxItemState.COMPLETED,
        background_job_id=job.id,
        resulting_model_id=model.id,
    )
    headers = {
        "Authorization": f"Bearer {create_access_token(owner.id, owner.username, scope='write')}"
    }

    response = client.delete(f"/api/v1/inbox/{row.id}", headers=headers)

    assert response.status_code == 204, response.text
    db_session.expire_all()
    refreshed = db_session.get(InboxItem, row.id)
    assert refreshed is not None
    assert refreshed.state == InboxItemState.DISMISSED
    assert refreshed.background_job_id is None
    assert db_session.get(Model, model.id) is not None
    assert db_session.get(File, artifact.id) is not None
    listed = client.get("/api/v1/inbox", headers=headers)
    assert listed.status_code == 200, listed.text
    assert row.id not in {item["id"] for item in listed.json()}


def test_batch_actions_cover_every_branch(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    headers = _headers(db_session, "batch-owner", admin=True)
    owner = _user(db_session, "batch-owner-user")

    resolve_calls: list[int] = []
    import_calls: list[int] = []

    async def fake_resolve(item_id: int) -> None:
        resolve_calls.append(item_id)

    async def fake_run_import(item_id: int, _selected_ids, _session_factory) -> None:
        import_calls.append(item_id)

    monkeypatch.setattr(inbox, "resolve", fake_resolve)
    monkeypatch.setattr(inbox, "run_import", fake_run_import)

    collection = Collection(
        name="Batch target", slug="batch-target", path="batch-target"
    )
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)

    set_collection_item = _make_item(db_session, owner)
    tag_item = _make_item(db_session, owner, requested_tags_json='["existing"]')
    retry_item = _make_item(
        db_session, owner, state=InboxItemState.FAILED, retryable=True, manifest_json=""
    )
    review_item = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json='{"kind": "direct", "title": "x"}',
    )
    not_ready_item = _make_item(db_session, owner, state=InboxItemState.CAPTURED)
    dismiss_item = _make_item(db_session, owner)

    set_collection = client.post(
        "/api/v1/inbox/batch",
        headers=headers,
        json={
            "item_ids": [set_collection_item.id],
            "action": "set_collection",
            "collection_id": collection.id,
        },
    )
    assert set_collection.status_code == 200
    assert set_collection.json()[0]["target_collection_id"] == collection.id

    response = client.post(
        "/api/v1/inbox/batch",
        headers=headers,
        json={
            "item_ids": [tag_item.id, tag_item.id],  # dedup via dict.fromkeys
            "action": "add_tags",
            "tags": ["new"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert set(body[0]["requested_tags"]) == {"existing", "new"}

    retried = client.post(
        "/api/v1/inbox/batch",
        headers=headers,
        json={"item_ids": [retry_item.id], "action": "retry"},
    )
    assert retried.status_code == 200
    assert retried.json()[0]["state"] == "captured"
    assert resolve_calls == [retry_item.id]

    imported = client.post(
        "/api/v1/inbox/batch",
        headers=headers,
        json={"item_ids": [review_item.id, not_ready_item.id], "action": "import"},
    )
    assert imported.status_code == 200
    # not_ready_item hits `continue` for action="import" (state != REVIEW) and is
    # dropped from the output entirely; only review_item is returned.
    assert {row["id"] for row in imported.json()} == {review_item.id}
    assert import_calls == [review_item.id]

    dismissed = client.post(
        "/api/v1/inbox/batch",
        headers=headers,
        json={"item_ids": [dismiss_item.id], "action": "dismiss"},
    )
    assert dismissed.status_code == 200
    assert dismissed.json()[0]["state"] == "dismissed"
