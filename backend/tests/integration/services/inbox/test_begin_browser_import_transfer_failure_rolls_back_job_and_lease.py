"""Defends begin browser import transfer failure rolls back job and lease at the services inbox integration boundary.

A regression could cross an owner boundary or lose a captured import during a retry.
"""

from __future__ import annotations

from ._inbox_internals_shared import (
    BackgroundJob,
    CaptureManifestV2,
    HTTPException,
    InboxItem,
    InboxItemCompletion,
    InboxItemResult,
    InboxItemResultState,
    InboxItemState,
    InboxItemUpdate,
    InboxSourceKind,
    Path,
    Session,
    StagingLease,
    _make_item,
    _make_user,
    get_session_factory,
    hashlib,
    import_resolvers,
    inbox,
    json,
    pytest,
    select,
    settings,
    staging_leases,
    timedelta,
    utcnow,
)


def test_begin_browser_import_transfer_failure_rolls_back_job_and_lease(
    db_session: Session, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _make_user(db_session, "lease-transfer-rollback")
    staged = tmp_path / "capture.stl"
    staged.write_bytes(b"solid x endsolid")
    item = _make_item(
        db_session,
        owner,
        source_kind=InboxSourceKind.BROWSER,
        state=InboxItemState.REVIEW,
        staging_key=str(staged),
        manifest_json=json.dumps({"kind": "browser_file", "filename": "capture.stl"}),
    )
    lease = staging_leases.create_review_lease(
        db_session,
        inbox_item_id=item.id,
        owner_user_id=owner.id,
        path=staged,
        size_bytes=staged.stat().st_size,
        sha256="a" * 64,
    )
    original = (lease.id, lease.inbox_item_id, lease.path, lease.expires_at)
    db_session.commit()

    def fail_transfer(*_args, **_kwargs) -> StagingLease:
        raise staging_leases.StagingLeaseError("injected")

    monkeypatch.setattr(staging_leases, "transfer_inbox_to_job", fail_transfer)
    assert inbox._begin_import(item.id, [], get_session_factory()) is None

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, item.id)
        assert fresh is not None
        assert fresh.state == InboxItemState.FAILED
        assert fresh.error_code == "staging_expired"
        assert fresh.retryable is False
        assert not session.exec(select(BackgroundJob)).all()
        retained = session.get(StagingLease, original[0])
        assert retained is not None
        assert (retained.inbox_item_id, retained.path) == original[1:3]
        assert retained.expires_at.replace(tzinfo=None) == original[3].replace(
            tzinfo=None
        )


def test_retry_partial_reselects_only_failed_source_ids(db_session: Session) -> None:
    owner = _make_user(db_session, "retry-partial")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.COMPLETED,
        completion=InboxItemCompletion.PARTIAL,
        retryable=True,
        manifest_json=json.dumps(
            {"kind": "model_files", "selected_ids": ["ok", "bad"]}
        ),
    )
    db_session.add_all(
        [
            InboxItemResult(
                inbox_item_id=row.id,
                source_selection_id="ok",
                result_key="self",
                original_filename="ok.stl",
                state=InboxItemResultState.IMPORTED,
                retryable=False,
            ),
            InboxItemResult(
                inbox_item_id=row.id,
                source_selection_id="bad",
                result_key="self",
                original_filename="bad.stl",
                state=InboxItemResultState.FAILED,
                error_code="captured_artifact_trashed",
                retryable=True,
            ),
        ]
    )
    db_session.commit()

    retried = inbox.retry(db_session, row)

    assert retried.state == InboxItemState.REVIEW
    assert retried.completion is None
    assert inbox._json_dict(retried.manifest_json)["selected_ids"] == ["bad"]


def test_sanitize_source_url_rejects_non_http_scheme() -> None:
    with pytest.raises(ValueError, match="url_invalid"):
        inbox.sanitize_source_url("ftp://example.com/model.stl")


def test_sanitize_source_url_rejects_missing_hostname() -> None:
    with pytest.raises(ValueError, match="url_invalid"):
        inbox.sanitize_source_url("https:///model.stl")


def test_sanitize_source_url_keeps_port_and_strips_secrets() -> None:
    result = inbox.sanitize_source_url(
        "HTTPS://Example.com:8443/model?token=secret&view=files"
    )
    assert result == "https://example.com:8443/model?view=files"


def test_sanitize_source_url_rejects_userinfo_before_redaction() -> None:
    with pytest.raises(ValueError, match="url_invalid"):
        inbox.sanitize_source_url(
            "HTTPS://alice:password@Example.com/model?view=files"
            "&X-Amz-Credential=credential&x_amz.signature=signature"
            "&X-Amz-Security-Token=session#private"
        )


def test_sanitize_source_url_redacts_normalized_signed_query_keys() -> None:
    result = inbox.sanitize_source_url(
        "HTTPS://Example.com/model?view=files"
        "&X-Amz-Credential=credential&x_amz.signature=signature"
        "&X-Amz-Security-Token=session#private"
    )
    assert result == "https://example.com/model?view=files"


@pytest.mark.parametrize("requested", [["missing"], ["ok", "missing"], [""]])
def test_v2_import_selection_rejects_invalid_ids_without_fallback(
    db_session: Session, requested: list[str]
) -> None:
    owner = _make_user(db_session, "selection-validation")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps(
            {
                "schema_version": 2,
                "kind": "model_files",
                "files": [{"id": "ok"}, {"id": "other"}],
                "selected_ids": ["ok", "other"],
            }
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        inbox.validate_import_selection(row, requested)

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail == "file_selection_invalid"


def test_v2_import_selection_accepts_valid_subset_and_defaults_when_empty(
    db_session: Session,
) -> None:
    owner = _make_user(db_session, "selection-validation-valid")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps(
            {
                "schema_version": 2,
                "kind": "model_files",
                "files": [{"id": "ok"}, {"id": "other"}],
                "selected_ids": ["ok", "other"],
            }
        ),
    )

    assert inbox.validate_import_selection(row, ["other"]) == ["other"]
    assert inbox.validate_import_selection(row, []) == ["ok", "other"]


def test_update_rejects_invalid_v2_selection_before_persisting(
    db_session: Session,
) -> None:
    owner = _make_user(db_session, "selection-update-validation")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps(
            {
                "schema_version": 2,
                "kind": "model_files",
                "files": [{"id": "ok"}],
                "selected_ids": ["ok"],
            }
        ),
    )
    original = row.manifest_json

    with pytest.raises(HTTPException) as exc_info:
        inbox.update(db_session, owner, row, InboxItemUpdate(selected_ids=["bad"]))

    assert exc_info.value.detail == "file_selection_invalid"
    assert row.manifest_json == original


def test_update_empty_v2_selection_persists_manifest_default_selection(
    db_session: Session,
) -> None:
    owner = _make_user(db_session, "selection-update-empty")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps(
            {
                "schema_version": 2,
                "kind": "model_files",
                "source": {
                    "provider": "makerworld",
                    "canonical_url": "https://makerworld.com/en/models/1234-widget",
                    "source_item_id": "1234",
                    "source_revision": "1",
                    "adapter_version": "test",
                    "fields": {},
                    "tags": [],
                },
                "files": [
                    {
                        "id": "first",
                        "name": "first.stl",
                        "file_type": "stl",
                        "size": None,
                    },
                    {
                        "id": "second",
                        "name": "second.stl",
                        "file_type": "stl",
                        "size": None,
                    },
                ],
                "selected_ids": ["first"],
            }
        ),
    )

    updated = inbox.update(db_session, owner, row, InboxItemUpdate(selected_ids=[]))

    manifest = json.loads(updated.manifest_json)
    assert manifest["selected_ids"] == ["first"]
    # The persisted value must remain parseable by the strict V2 contract.
    CaptureManifestV2.from_dict(manifest)


def test_json_dict_returns_empty_on_bad_json() -> None:
    assert inbox._json_dict("not json") == {}
    assert inbox._json_dict("[]") == {}  # valid JSON but not a dict
    assert inbox._json_dict("") == {}


def test_requested_tags_returns_empty_on_bad_json() -> None:
    assert inbox.requested_tags("not json") == []
    assert inbox.requested_tags("{}") == []  # valid JSON but not a list
    assert inbox.requested_tags(json.dumps(["a", "b"])) == ["a", "b"]


def test_list_visible_scopes_non_admin_results_to_owner(db_session: Session) -> None:
    owner = _make_user(db_session, "inbox-owner", admin=False)
    other = _make_user(db_session, "inbox-other", admin=False)
    mine = _make_item(db_session, owner)
    _make_item(db_session, other)
    done = _make_item(db_session, owner, state=InboxItemState.COMPLETED)

    owner_rows = inbox.list_visible(db_session, owner)
    assert {row.id for row in owner_rows} == {mine.id, done.id}


def test_list_visible_can_exclude_completed_owner_items(db_session: Session) -> None:
    owner = _make_user(db_session, "active-inbox-owner", admin=False)
    mine = _make_item(db_session, owner)
    done = _make_item(db_session, owner, state=InboxItemState.COMPLETED)

    owner_active = inbox.list_visible(db_session, owner, include_completed=False)

    assert {row.id for row in owner_active} == {mine.id}
    assert done.id not in {row.id for row in owner_active}


def test_prune_history_removes_only_old_terminal_items(db_session: Session) -> None:
    owner = _make_user(db_session, "prune-owner")
    old_done = _make_item(db_session, owner, state=InboxItemState.COMPLETED)
    old_done.updated_at = utcnow() - timedelta(days=40)
    recent_done = _make_item(db_session, owner, state=InboxItemState.COMPLETED)
    still_review = _make_item(db_session, owner, state=InboxItemState.REVIEW)
    still_review.updated_at = utcnow() - timedelta(days=40)
    db_session.add_all([old_done, recent_done, still_review])
    db_session.commit()
    old_done_id, recent_done_id, still_review_id = (
        old_done.id,
        recent_done.id,
        still_review.id,
    )

    pruned = inbox.prune_history(retention_days=30)

    assert pruned == 1
    with get_session_factory().scoped_session() as session:
        assert session.get(InboxItem, old_done_id) is None
        assert session.get(InboxItem, recent_done_id) is not None
        assert session.get(InboxItem, still_review_id) is not None


def test_update_rejects_terminal_states(db_session: Session) -> None:
    owner = _make_user(db_session, "update-owner")
    row = _make_item(db_session, owner, state=InboxItemState.IMPORTING)
    with pytest.raises(HTTPException) as exc:
        inbox.update(db_session, owner, row, InboxItemUpdate())
    assert exc.value.status_code == 409


def test_update_merges_selected_ids_into_manifest(db_session: Session) -> None:
    owner = _make_user(db_session, "update-owner2")
    row = _make_item(db_session, owner, manifest_json=json.dumps({"kind": "archive"}))
    updated = inbox.update(
        db_session, owner, row, InboxItemUpdate(selected_ids=["a.stl", "b.stl"])
    )
    manifest = json.loads(updated.manifest_json)
    assert manifest["selected_ids"] == ["a.stl", "b.stl"]
    assert manifest["kind"] == "archive"


def test_update_root_collection_requires_superuser(db_session: Session) -> None:
    owner = _make_user(db_session, "update-owner3", admin=False)
    row = _make_item(db_session, owner)
    with pytest.raises(HTTPException) as exc:
        inbox.update(db_session, owner, row, InboxItemUpdate(collection_id=None))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_resolve_ignores_item_in_wrong_state(db_session: Session) -> None:
    owner = _make_user(db_session, "resolve-wrong-state")
    row = _make_item(db_session, owner, state=InboxItemState.REVIEW)
    await inbox.resolve(row.id)
    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.REVIEW


def test_dismiss_rejects_item_while_resolving(db_session: Session) -> None:
    owner = _make_user(db_session, "dismiss-resolving")
    managed = settings.incoming_dir / "inbox" / "dismiss-resolving" / "source.zip"
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_bytes(b"resolver-owned")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.RESOLVING,
        staging_key=str(managed),
    )

    with pytest.raises(HTTPException, match="pending_import_busy"):
        inbox.dismiss(db_session, row)

    db_session.rollback()
    fresh = db_session.get(InboxItem, row.id)
    assert fresh is not None
    assert fresh.state == InboxItemState.RESOLVING
    assert fresh.staging_key == str(managed)
    assert managed.exists()
    managed.unlink()
    managed.parent.rmdir()


def test_resolve_completion_does_not_resurrect_dismissed_item_or_leak_staging(
    db_session: Session,
) -> None:
    owner = _make_user(db_session, "resolve-dismiss-race")
    managed = settings.incoming_dir / "inbox" / "resolve-dismiss-race" / "source.zip"
    managed.parent.mkdir(parents=True, exist_ok=True)
    managed.write_bytes(b"resolver-owned")
    row = _make_item(db_session, owner, state=InboxItemState.DISMISSED)
    staging_leases.create_review_lease(
        db_session,
        inbox_item_id=row.id,
        owner_user_id=owner.id,
        path=managed,
        size_bytes=managed.stat().st_size,
        sha256=hashlib.sha256(managed.read_bytes()).hexdigest(),
    )
    db_session.commit()

    inbox._finish_resolve(
        row.id,
        {"kind": "archive", "title": "must-not-publish"},
        managed,
    )

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh is not None
        assert fresh.state == InboxItemState.DISMISSED
        assert fresh.manifest_json == "{}"
        assert fresh.staging_key is None
        assert (
            session.exec(
                select(StagingLease).where(StagingLease.inbox_item_id == row.id)
            ).first()
            is None
        )
    assert not managed.exists()


@pytest.mark.asyncio
async def test_resolve_marks_failed_when_source_url_missing(
    db_session: Session,
) -> None:
    owner = _make_user(db_session, "resolve-no-url")
    row = _make_item(db_session, owner, source_url=None)
    await inbox.resolve(row.id)
    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.FAILED
        assert fresh.retryable is True


@pytest.mark.asyncio
async def test_resolve_collection_success_builds_manifest(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _make_user(db_session, "resolve-collection")
    row = _make_item(db_session, owner)
    monkeypatch.setattr(
        import_resolvers, "classify_collection", lambda _url: "printables"
    )

    async def fake_resolve_collection_url(_url: str):
        return "My Collection", [
            import_resolvers.CollectionMember(
                page_url="https://example.com/model/1", title="Part", source_id="1"
            )
        ]

    monkeypatch.setattr(
        import_resolvers, "resolve_collection_url", fake_resolve_collection_url
    )

    await inbox.resolve(row.id)

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.REVIEW
        manifest = json.loads(fresh.manifest_json)
        assert manifest["kind"] == "collection"
        assert manifest["members"][0]["id"] == "1"


@pytest.mark.asyncio
async def test_resolve_collection_failure_marks_item_failed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _make_user(db_session, "resolve-collection-fail")
    row = _make_item(db_session, owner)
    monkeypatch.setattr(
        import_resolvers, "classify_collection", lambda _url: "printables"
    )

    async def no_result(_url: str):
        return None

    monkeypatch.setattr(import_resolvers, "resolve_collection_url", no_result)

    await inbox.resolve(row.id)

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.FAILED
        assert fresh.error_code == "collection_resolve_failed"


@pytest.mark.asyncio
async def test_resolve_model_files_listing_builds_manifest(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _make_user(db_session, "resolve-model-files")
    row = _make_item(db_session, owner)
    monkeypatch.setattr(import_resolvers, "classify_collection", lambda _url: None)

    async def fake_list_model_files(_url: str):
        return "Bracket", [
            import_resolvers.ModelFile(
                file_id="f1", name="bracket.stl", file_type="stl", size=10
            )
        ]

    monkeypatch.setattr(import_resolvers, "list_model_files", fake_list_model_files)

    await inbox.resolve(row.id)

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.REVIEW
        manifest = json.loads(fresh.manifest_json)
        assert manifest["kind"] == "model_files"
        assert manifest["files"][0]["id"] == "f1"
