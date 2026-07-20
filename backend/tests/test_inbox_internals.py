"""Unit coverage for app/services/inbox.py's internal orchestration —
resolve/run_import/retry/dismiss/reconcile/prune — that the API-level tests
in test_inbox_api.py don't reach."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    Collection,
    InboxItem,
    InboxItemState,
    User,
)
from app.db.session import get_session_factory
from app.schemas.inbox import InboxItemUpdate
from app.services import import_resolvers, importer, inbox
from app.services.auth import hash_password
from app.services.jobs import registry


def _make_user(session: Session, username: str, *, admin: bool = True) -> User:
    user = User(username=username, hashed_password=hash_password("Password123"), is_superuser=admin)
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _make_collection(session: Session, path: str = "vault") -> Collection:
    col = Collection(name=path, slug=path, path=path)
    session.add(col)
    session.commit()
    session.refresh(col)
    return col


def _make_item(session: Session, owner: User, **overrides) -> InboxItem:
    defaults = dict(
        owner_user_id=owner.id,
        source_url="https://example.com/model",
        source_hostname="example.com",
        state=InboxItemState.CAPTURED,
    )
    defaults.update(overrides)
    row = InboxItem(**defaults)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# --------------------------------------------------------------------------- #
# sanitize_source_url / _json_dict / requested_tags
# --------------------------------------------------------------------------- #


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


def test_json_dict_returns_empty_on_bad_json() -> None:
    assert inbox._json_dict("not json") == {}
    assert inbox._json_dict("[]") == {}  # valid JSON but not a dict
    assert inbox._json_dict("") == {}


def test_requested_tags_returns_empty_on_bad_json() -> None:
    assert inbox.requested_tags("not json") == []
    assert inbox.requested_tags("{}") == []  # valid JSON but not a list
    assert inbox.requested_tags(json.dumps(["a", "b"])) == ["a", "b"]


# --------------------------------------------------------------------------- #
# list_visible
# --------------------------------------------------------------------------- #


def test_list_visible_scopes_to_owner_and_can_exclude_completed(db_session: Session) -> None:
    owner = _make_user(db_session, "inbox-owner", admin=False)
    other = _make_user(db_session, "inbox-other", admin=False)
    admin = _make_user(db_session, "inbox-admin", admin=True)
    mine = _make_item(db_session, owner)
    _make_item(db_session, other)
    done = _make_item(db_session, owner, state=InboxItemState.COMPLETED)

    owner_rows = inbox.list_visible(db_session, owner)
    assert {row.id for row in owner_rows} == {mine.id, done.id}

    owner_active = inbox.list_visible(db_session, owner, include_completed=False)
    assert {row.id for row in owner_active} == {mine.id}

    admin_rows = inbox.list_visible(db_session, admin)
    assert {row.id for row in admin_rows} >= {mine.id, done.id}


# --------------------------------------------------------------------------- #
# prune_history
# --------------------------------------------------------------------------- #


def test_prune_history_removes_only_old_terminal_items(db_session: Session) -> None:
    owner = _make_user(db_session, "prune-owner")
    old_done = _make_item(db_session, owner, state=InboxItemState.COMPLETED)
    old_done.updated_at = utcnow() - timedelta(days=40)
    recent_done = _make_item(db_session, owner, state=InboxItemState.COMPLETED)
    still_review = _make_item(db_session, owner, state=InboxItemState.REVIEW)
    still_review.updated_at = utcnow() - timedelta(days=40)
    db_session.add_all([old_done, recent_done, still_review])
    db_session.commit()
    old_done_id, recent_done_id, still_review_id = old_done.id, recent_done.id, still_review.id

    pruned = inbox.prune_history(retention_days=30)

    assert pruned == 1
    with get_session_factory().scoped_session() as session:
        assert session.get(InboxItem, old_done_id) is None
        assert session.get(InboxItem, recent_done_id) is not None
        assert session.get(InboxItem, still_review_id) is not None


# --------------------------------------------------------------------------- #
# update()
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# resolve()
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_resolve_ignores_item_in_wrong_state(db_session: Session) -> None:
    owner = _make_user(db_session, "resolve-wrong-state")
    row = _make_item(db_session, owner, state=InboxItemState.REVIEW)
    await inbox.resolve(row.id)
    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.REVIEW


@pytest.mark.asyncio
async def test_resolve_marks_failed_when_source_url_missing(db_session: Session) -> None:
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
    monkeypatch.setattr(import_resolvers, "classify_collection", lambda _url: "printables")

    async def fake_resolve_collection_url(_url: str):
        return "My Collection", [
            import_resolvers.CollectionMember(
                page_url="https://example.com/model/1", title="Part", source_id="1"
            )
        ]

    monkeypatch.setattr(import_resolvers, "resolve_collection_url", fake_resolve_collection_url)

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
    monkeypatch.setattr(import_resolvers, "classify_collection", lambda _url: "printables")

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
            import_resolvers.ModelFile(file_id="f1", name="bracket.stl", file_type="stl", size=10)
        ]

    monkeypatch.setattr(import_resolvers, "list_model_files", fake_list_model_files)

    await inbox.resolve(row.id)

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.REVIEW
        manifest = json.loads(fresh.manifest_json)
        assert manifest["kind"] == "model_files"
        assert manifest["files"][0]["id"] == "f1"


@pytest.mark.asyncio
async def test_resolve_direct_download_archive_manifest(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "resolve-archive")
    row = _make_item(db_session, owner)
    monkeypatch.setattr(import_resolvers, "classify_collection", lambda _url: None)

    async def no_listing(_url: str):
        return None

    monkeypatch.setattr(import_resolvers, "list_model_files", no_listing)

    async def no_page_url(_url: str):
        return None

    monkeypatch.setattr(import_resolvers, "resolve_page_url", no_page_url)

    staged = tmp_path / "download.bin"
    staged.write_bytes(b"pk-zip-stub")

    async def fake_download(_url: str):
        return staged, "bundle.zip"

    monkeypatch.setattr(importer, "download_to_staging", fake_download)
    monkeypatch.setattr(
        importer,
        "inspect_archive",
        lambda _path: [
            importer.ArchiveEntry(name="a.stl", size_bytes=1, file_type="stl", is_image=False),
            importer.ArchiveEntry(name="readme.txt", size_bytes=1, file_type=None, is_image=False),
        ],
    )

    await inbox.resolve(row.id)

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.REVIEW
        manifest = json.loads(fresh.manifest_json)
        assert manifest["kind"] == "archive"
        assert [entry["id"] for entry in manifest["entries"]] == ["a.stl"]
        assert fresh.staging_key is not None
        assert Path(fresh.staging_key).exists()


@pytest.mark.asyncio
async def test_resolve_direct_download_non_archive_manifest(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "resolve-direct")
    row = _make_item(db_session, owner)
    monkeypatch.setattr(import_resolvers, "classify_collection", lambda _url: None)

    async def no_listing(_url: str):
        return None

    monkeypatch.setattr(import_resolvers, "list_model_files", no_listing)

    async def no_page_url(_url: str):
        return None

    monkeypatch.setattr(import_resolvers, "resolve_page_url", no_page_url)

    staged = tmp_path / "download.stl"
    staged.write_bytes(b"solid x endsolid")

    async def fake_download(_url: str):
        return staged, "model.stl"

    monkeypatch.setattr(importer, "download_to_staging", fake_download)

    await inbox.resolve(row.id)

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.REVIEW
        manifest = json.loads(fresh.manifest_json)
        assert manifest["kind"] == "direct"
    assert not staged.exists()


@pytest.mark.asyncio
async def test_resolve_unexpected_exception_marks_item_failed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _make_user(db_session, "resolve-boom")
    row = _make_item(db_session, owner)

    def boom(_url: str):
        raise RuntimeError("boom")

    monkeypatch.setattr(import_resolvers, "classify_collection", boom)

    await inbox.resolve(row.id)

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.FAILED
        assert fresh.retryable is True


# --------------------------------------------------------------------------- #
# run_import()
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_import_ignores_item_not_in_review(db_session: Session) -> None:
    owner = _make_user(db_session, "run-import-wrong-state")
    row = _make_item(db_session, owner, state=InboxItemState.CAPTURED)
    await inbox.run_import(row.id, [], get_session_factory())
    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.CAPTURED


@pytest.mark.asyncio
async def test_run_import_direct_completes_and_marks_model(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "run-import-direct")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps({"kind": "direct"}),
    )

    staged = tmp_path / "model.stl"
    staged.write_bytes(b"solid x endsolid")

    async def fake_download_assets(_url: str):
        return [(staged, "model.stl")]

    monkeypatch.setattr(inbox, "_download_assets", fake_download_assets)

    def fake_import_assets(*, job_id: str, **_kwargs) -> None:
        registry.update(job_id, state="completed", model_id=42)

    monkeypatch.setattr(importer, "import_assets", fake_import_assets)

    await inbox.run_import(row.id, [], get_session_factory())

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.COMPLETED
        assert fresh.resulting_model_id == 42
        assert fresh.completed_at is not None


@pytest.mark.asyncio
async def test_run_import_archive_selection_and_missing_staging(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _make_user(db_session, "run-import-archive-missing")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps(
            {"kind": "archive", "entries": [{"id": "a.stl"}, {"id": "b.stl"}]}
        ),
        staging_key=None,
    )

    await inbox.run_import(row.id, ["a.stl"], get_session_factory())

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.FAILED
        assert fresh.retryable is True


@pytest.mark.asyncio
async def test_run_import_archive_completes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "run-import-archive-ok")
    staged_archive = tmp_path / "bundle.zip"
    staged_archive.write_bytes(b"pk-zip-stub")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps(
            {"kind": "archive", "entries": [{"id": "a.stl"}, {"id": "b.stl"}]}
        ),
        staging_key=str(staged_archive),
    )

    extracted = tmp_path / "a.stl"
    extracted.write_bytes(b"solid x endsolid")
    monkeypatch.setattr(
        importer, "extract_selected", lambda _path, names: [(extracted, "a.stl")] if "a.stl" in names else []
    )

    def fake_import_assets(*, job_id: str, **_kwargs) -> None:
        registry.update(job_id, state="completed", model_id=7)

    monkeypatch.setattr(importer, "import_assets", fake_import_assets)

    await inbox.run_import(row.id, ["a.stl"], get_session_factory())

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.COMPLETED
        assert fresh.resulting_model_id == 7
        assert fresh.staging_key is None
    assert not staged_archive.exists()


@pytest.mark.asyncio
async def test_run_import_model_files_completes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "run-import-model-files")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps(
            {
                "kind": "model_files",
                "files": [{"id": "f1", "name": "bracket.stl", "file_type": "stl", "size": 10}],
            }
        ),
    )

    async def fake_resolve_selected_download(_url, chosen):
        assert chosen[0].file_id == "f1"
        return ["https://example.com/download/f1"]

    monkeypatch.setattr(import_resolvers, "resolve_selected_download", fake_resolve_selected_download)

    staged = tmp_path / "bracket.stl"
    staged.write_bytes(b"solid x endsolid")

    async def fake_download_assets(_url: str):
        return [(staged, "bracket.stl")]

    monkeypatch.setattr(inbox, "_download_assets", fake_download_assets)

    def fake_import_assets(*, job_id: str, **_kwargs) -> None:
        registry.update(job_id, state="completed", model_id=9)

    monkeypatch.setattr(importer, "import_assets", fake_import_assets)

    await inbox.run_import(row.id, [], get_session_factory())

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.COMPLETED
        assert fresh.resulting_model_id == 9


@pytest.mark.asyncio
async def test_run_import_collection_completes(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "run-import-collection")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps(
            {
                "kind": "collection",
                "members": [{"id": "m1", "page_url": "https://example.com/model/1"}],
            }
        ),
    )

    staged = tmp_path / "member.stl"
    staged.write_bytes(b"solid x endsolid")

    async def fake_download_assets(_url: str):
        return [(staged, "member.stl")]

    monkeypatch.setattr(inbox, "_download_assets", fake_download_assets)

    def fake_import_assets(*, job_id: str, **_kwargs) -> None:
        registry.update(job_id, state="completed", model_id=11)

    monkeypatch.setattr(importer, "import_assets", fake_import_assets)

    await inbox.run_import(row.id, [], get_session_factory())

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.COMPLETED
        assert fresh.resulting_model_id == 11


@pytest.mark.asyncio
async def test_run_import_job_not_completed_marks_failed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    owner = _make_user(db_session, "run-import-job-failed")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps({"kind": "direct"}),
    )

    staged = tmp_path / "model.stl"
    staged.write_bytes(b"solid x endsolid")

    async def fake_download_assets(_url: str):
        return [(staged, "model.stl")]

    monkeypatch.setattr(inbox, "_download_assets", fake_download_assets)

    def fake_import_assets(*, job_id: str, **_kwargs) -> None:
        registry.update(job_id, state="failed", error="ingest_exploded")

    monkeypatch.setattr(importer, "import_assets", fake_import_assets)

    await inbox.run_import(row.id, [], get_session_factory())

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.FAILED
        assert fresh.error_code == "ingest_exploded"
        assert fresh.retryable is True


@pytest.mark.asyncio
async def test_run_import_exception_marks_failed(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = _make_user(db_session, "run-import-boom")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps({"kind": "direct"}),
    )

    async def boom(_url: str):
        raise RuntimeError("network exploded")

    monkeypatch.setattr(inbox, "_download_assets", boom)

    await inbox.run_import(row.id, [], get_session_factory())

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.FAILED
        assert fresh.retryable is True


@pytest.mark.asyncio
async def test_run_import_requires_target_collection_access(db_session: Session) -> None:
    owner = _make_user(db_session, "run-import-no-access", admin=False)
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.REVIEW,
        target_collection_id=None,
        manifest_json=json.dumps({"kind": "direct"}),
    )
    with pytest.raises(HTTPException) as exc:
        await inbox.run_import(row.id, [], get_session_factory())
    assert exc.value.status_code == 403


# --------------------------------------------------------------------------- #
# retry() / dismiss()
# --------------------------------------------------------------------------- #


def test_retry_requires_failed_and_retryable(db_session: Session) -> None:
    owner = _make_user(db_session, "retry-owner")
    row = _make_item(db_session, owner, state=InboxItemState.REVIEW)
    with pytest.raises(HTTPException) as exc:
        inbox.retry(db_session, row)
    assert exc.value.status_code == 409


def test_retry_returns_to_review_when_manifest_present(db_session: Session) -> None:
    owner = _make_user(db_session, "retry-owner2")
    row = _make_item(
        db_session,
        owner,
        state=InboxItemState.FAILED,
        retryable=True,
        manifest_json=json.dumps({"kind": "direct"}),
    )
    updated = inbox.retry(db_session, row)
    assert updated.state == InboxItemState.REVIEW


def test_retry_returns_to_captured_without_manifest(db_session: Session) -> None:
    owner = _make_user(db_session, "retry-owner3")
    row = _make_item(db_session, owner, state=InboxItemState.FAILED, retryable=True)
    updated = inbox.retry(db_session, row)
    assert updated.state == InboxItemState.CAPTURED


def test_dismiss_rejects_importing_item(db_session: Session) -> None:
    owner = _make_user(db_session, "dismiss-owner")
    row = _make_item(db_session, owner, state=InboxItemState.IMPORTING)
    with pytest.raises(HTTPException) as exc:
        inbox.dismiss(db_session, row)
    assert exc.value.status_code == 409


def test_dismiss_cleans_up_staging_directory(db_session: Session, tmp_path: Path) -> None:
    owner = _make_user(db_session, "dismiss-owner2")
    staging_dir = tmp_path / "inbox-item"
    staging_dir.mkdir()
    staged_file = staging_dir / "source.stl"
    staged_file.write_bytes(b"solid x endsolid")
    row = _make_item(db_session, owner, state=InboxItemState.REVIEW, staging_key=str(staged_file))

    inbox.dismiss(db_session, row)

    assert row.state == InboxItemState.DISMISSED
    assert row.staging_key is None
    assert not staged_file.exists()
    assert not staging_dir.exists()


# --------------------------------------------------------------------------- #
# reconcile_interrupted_items()
# --------------------------------------------------------------------------- #


def test_reconcile_marks_resolving_items_failed(db_session: Session) -> None:
    owner = _make_user(db_session, "reconcile-resolving")
    row = _make_item(db_session, owner, state=InboxItemState.RESOLVING)
    count = inbox.reconcile_interrupted_items()
    assert count >= 1
    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.FAILED
        assert fresh.error_code == "import_interrupted"


def test_reconcile_completes_importing_item_with_finished_job(db_session: Session) -> None:
    owner = _make_user(db_session, "reconcile-importing-ok")
    job_id = registry.create(owner_user_id=owner.id)
    registry.update(job_id, state="completed", model_id=5)
    row = _make_item(
        db_session, owner, state=InboxItemState.IMPORTING, background_job_id=job_id
    )

    inbox.reconcile_interrupted_items()

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.COMPLETED
        assert fresh.resulting_model_id == 5


def test_reconcile_fails_importing_item_without_finished_job(db_session: Session) -> None:
    owner = _make_user(db_session, "reconcile-importing-fail")
    row = _make_item(db_session, owner, state=InboxItemState.IMPORTING, background_job_id=None)

    inbox.reconcile_interrupted_items()

    with get_session_factory().scoped_session() as session:
        fresh = session.get(InboxItem, row.id)
        assert fresh.state == InboxItemState.FAILED
        assert fresh.error_code == "import_interrupted"
