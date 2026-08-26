"""Requirement coverage for the URL/ZIP importer service boundaries."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from printstash_core.files import ArchivePolicyError

from app.services import importer


class _StreamResponse:
    is_redirect = True
    headers: dict[str, str] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _StreamingClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def stream(self, *_args, **_kwargs):
        return _StreamResponse()


def _pending_archive() -> importer._PendingArchive:
    return importer._PendingArchive(
        path=Path("pending.zip"),
        archive_name="pending.zip",
        owner_user_id=1,
        entries=[],
    )


@pytest.mark.asyncio
async def test_redirect_without_location_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(importer, "_resolve_or_raise", lambda _url: object())
    monkeypatch.setattr(importer, "pinned_transport", lambda _target: object())
    monkeypatch.setattr(
        importer.httpx, "AsyncClient", lambda **_kwargs: _StreamingClient()
    )

    with pytest.raises(importer.ImportError_, match="url_redirect_without_location"):
        await importer.download_to_staging("https://example.test/file.stl")


def test_extract_selected_translates_archive_policy_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def reject(*_args, **_kwargs):
        raise ArchivePolicyError("archive_path_unsafe")

    monkeypatch.setattr(importer, "extract_selected_archive_entries", reject)

    with pytest.raises(importer.ImportError_, match="archive_path_unsafe"):
        importer.extract_selected(tmp_path / "unsafe.zip", ["../part.stl"])


def test_archive_registry_missing_claim_returns_none() -> None:
    registry = importer._ArchiveRegistry()

    result = registry.claim("missing")

    assert result is None


def test_archive_registry_first_claim_returns_pending_archive() -> None:
    registry = importer._ArchiveRegistry()
    pending = _pending_archive()
    archive_id = registry.add(pending)

    result = registry.claim(archive_id)

    assert result is pending


def test_archive_registry_replayed_claim_returns_none() -> None:
    registry = importer._ArchiveRegistry()
    archive_id = registry.add(_pending_archive())
    registry.claim(archive_id)

    result = registry.claim(archive_id)

    assert result is None


def test_archive_registry_prunes_and_unlinks_expired_file(tmp_path: Path) -> None:
    registry = importer._ArchiveRegistry()
    stale_path = tmp_path / "stale.zip"
    stale_path.write_bytes(b"zip")
    stale = importer._PendingArchive(
        path=stale_path,
        archive_name="stale.zip",
        owner_user_id=1,
        entries=[],
        created_at=time.time() - registry._TTL - 1,
    )
    registry._items["stale"] = stale

    registry.add(
        importer._PendingArchive(
            path=tmp_path / "fresh.zip",
            archive_name="fresh.zip",
            owner_user_id=1,
            entries=[],
        )
    )

    assert "stale" not in registry._items
    assert not stale_path.exists()


def test_ingest_one_file_removes_and_skips_unsupported_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    staged = tmp_path / "notes.txt"
    staged.write_text("not a printable artifact")
    monkeypatch.setattr(importer.registry, "create", lambda **_kwargs: "child")

    result = importer._ingest_one_file(
        staged,
        "folder/notes.txt",
        collection=None,
        tags=None,
        source_url=None,
        model_name=None,
        actor_user_id=1,
        session_factory=object(),
    )

    assert result is None
    assert not staged.exists()


def test_ingest_one_file_reports_missing_child_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    staged = tmp_path / "part.stl"
    staged.write_bytes(b"solid part")
    monkeypatch.setattr(importer.registry, "create", lambda **_kwargs: "child")
    monkeypatch.setattr(importer.registry, "get", lambda _job_id: None)
    monkeypatch.setattr(importer, "ingest_mesh", lambda **_kwargs: None)

    result = importer._ingest_one_file(
        staged,
        "part.stl",
        collection="parts",
        tags=None,
        source_url=None,
        model_name=None,
        actor_user_id=1,
        session_factory=object(),
    )

    assert result == {"name": "part.stl", "error": "unknown_error"}


def test_ingest_one_file_contains_child_exception_and_cleans_staging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    staged = tmp_path / "part.stl"
    staged.write_bytes(b"solid part")
    monkeypatch.setattr(importer.registry, "create", lambda **_kwargs: "child")

    def fail(**_kwargs):
        raise RuntimeError("parser failed")

    monkeypatch.setattr(importer, "ingest_mesh", fail)

    result = importer._ingest_one_file(
        staged,
        "part.stl",
        collection=None,
        tags=None,
        source_url=None,
        model_name=None,
        actor_user_id=1,
        session_factory=object(),
    )

    assert result == {"name": "part.stl", "error": "parser failed"}
    assert not staged.exists()


def test_import_assets_empty_input_fails_parent_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    updates: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        importer.registry,
        "update",
        lambda job_id, **fields: updates.append((job_id, fields)),
    )

    importer.import_assets(
        job_id="parent",
        staged_files=[],
        collection=None,
        tags=None,
        source_url=None,
        actor_user_id=1,
        session_factory=object(),
    )

    assert updates == [("parent", {"state": "failed", "error": "no_importable_files"})]
