"""Browser capture persistence remains deterministic around patched egress.

These integration cases cover resolution, durable import, partial retry, and API-key
capture without placing mocked collaborators in the E2E tier.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from printstash_core.imports import CaptureManifestV2, ResolvedAsset, StagedAsset
from sqlmodel import Session, select

from app.core.config import _overlay, settings
from app.db.models import ArtifactProvenanceLink, File, InboxItem, Model, User
from app.services import inbox
from app.services.auth import create_api_key
from app.services.hashing import sha256_file
from app.services.storage_backend import LocalStorageBackend, bind_backend
from tests.paths import TEST_DATA_DIR


@pytest.fixture(autouse=True)
def _use_file_backed_db(file_backed_integration_db: None) -> None:
    """Let request, worker, and assertion sessions use separate connections."""


def _configure_storage(tmp_path: Path) -> None:
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"
    bind_backend(LocalStorageBackend())


def _captured_manifest() -> CaptureManifestV2:
    return CaptureManifestV2.from_dict(
        {
            "schema_version": 2,
            "kind": "model_files",
            "source": {
                "provider": "printables",
                "canonical_url": "https://www.printables.com/model/3161-3d-benchy",
                "source_item_id": "3161",
                "source_revision": None,
                "adapter_version": "fixture-v1",
                "fields": {"title": {"value": "3D Benchy", "origin": "confirmed"}},
            },
            "files": [
                {"id": "stl-1", "name": "benchy.stl", "file_type": "stl", "size": 12}
            ],
            "selected_ids": ["stl-1"],
        }
    )


def _stage_fixture_asset(tmp_path: Path, manifest: CaptureManifestV2) -> StagedAsset:
    source = TEST_DATA_DIR / "sample.gcode"
    staged = settings.incoming_dir / f"{tmp_path.name}-benchy.gcode"
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, staged)
    resolved = ResolvedAsset(
        manifest=manifest,
        source_selection_id="stl-1",
        source_file_id="stl-1",
        source_filename="benchy.gcode",
        download_url="https://fixture.invalid/benchy.gcode",
        source_item_id="3161",
    )
    return StagedAsset(resolved, staged, "self", sha256_file(staged))


def test_browser_capture_resolves_offline_printables_fixture_to_review(
    tmp_path: Path,
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_storage(tmp_path)
    owner = db_session.exec(select(User).where(User.username == "test-writer")).one()
    monkeypatch.setattr(inbox.importer, "validate_public_url", lambda _url: None)

    async def fixture_capture(_url: str) -> CaptureManifestV2:
        return _captured_manifest()

    monkeypatch.setattr(
        inbox.import_resolvers, "resolve_capture_manifest", fixture_capture
    )

    captured = client.post(
        "/api/v1/inbox",
        headers=auth_headers,
        json={"url": "https://www.printables.com/model/3161-3d-benchy"},
    )

    assert captured.status_code == 202, captured.text
    detail = client.get(f"/api/v1/inbox/{captured.json()['id']}", headers=auth_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["state"] == "review"
    assert detail.json()["manifest"] == _captured_manifest().to_dict()
    assert db_session.exec(select(InboxItem)).one().owner_user_id == owner.id


def test_offline_capture_import_recapture_deduplicates_durable_artifact(
    tmp_path: Path,
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_storage(tmp_path)
    manifest = CaptureManifestV2.from_dict(
        {
            **_captured_manifest().to_dict(),
            "files": [
                {
                    "id": "stl-1",
                    "name": "benchy.gcode",
                    "file_type": "gcode",
                    "size": 1,
                }
            ],
        }
    )
    monkeypatch.setattr(inbox.importer, "validate_public_url", lambda _url: None)

    async def fixture_capture(_url: str) -> CaptureManifestV2:
        return manifest

    async def fixture_resolved(
        _url: str,
        _manifest: CaptureManifestV2,
        _ids: list[str],
        _context: object,
    ) -> list[ResolvedAsset]:
        return [_stage_fixture_asset(tmp_path, manifest).resolved]

    async def fixture_stage(resolved: ResolvedAsset) -> list[StagedAsset]:
        return [_stage_fixture_asset(tmp_path, resolved.manifest)]

    monkeypatch.setattr(
        inbox.import_resolvers, "resolve_capture_manifest", fixture_capture
    )
    monkeypatch.setattr(
        inbox.import_resolvers, "resolve_selected_assets", fixture_resolved
    )
    monkeypatch.setattr(inbox, "_download_resolved_asset", fixture_stage)

    def capture_and_import() -> dict:
        captured = client.post(
            "/api/v1/inbox",
            headers=auth_headers,
            json={"url": "https://www.printables.com/model/3161-3d-benchy"},
        )
        assert captured.status_code == 202, captured.text
        item_id = captured.json()["id"]
        imported = client.post(
            f"/api/v1/inbox/{item_id}/import",
            headers=auth_headers,
            json={"selected_ids": ["stl-1"]},
        )
        assert imported.status_code == 200, imported.text
        return client.get(f"/api/v1/inbox/{item_id}", headers=auth_headers).json()

    first = capture_and_import()
    assert first["state"] == "completed"
    assert first["results"][0]["state"] == "imported"
    assert len(db_session.exec(select(File)).all()) == 1
    assert len(db_session.exec(select(ArtifactProvenanceLink)).all()) == 1
    assert len(db_session.exec(select(Model)).all()) == 1

    model_id = first["results"][0]["model_id"]
    source = client.get(
        f"/api/v1/models/{model_id}/provenance", headers=auth_headers
    ).json()["sources"][0]
    overridden = client.patch(
        f"/api/v1/models/{model_id}/provenance/{source['id']}",
        headers=auth_headers,
        json={"overrides": {"title": "My Benchy"}},
    )
    assert overridden.status_code == 200, overridden.text
    title = overridden.json()["sources"][0]["fields"][0]
    assert title["effective_value"] == "My Benchy"
    assert title["effective_origin"] == "user"

    second = capture_and_import()
    assert second["state"] == "completed"
    assert second["results"][0]["state"] == "deduplicated"
    assert len(db_session.exec(select(File)).all()) == 1
    preserved = client.get(
        f"/api/v1/models/{model_id}/provenance", headers=auth_headers
    )
    assert preserved.json()["sources"][0]["fields"][0]["effective_value"] == (
        "My Benchy"
    )


def test_offline_capture_partial_result_retries_only_failed_selection(
    tmp_path: Path,
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_storage(tmp_path)
    manifest = CaptureManifestV2.from_dict(
        {
            **_captured_manifest().to_dict(),
            "files": [
                {"id": "good", "name": "good.gcode", "file_type": "gcode", "size": 1},
                {"id": "bad", "name": "bad.gcode", "file_type": "gcode", "size": 1},
            ],
            "selected_ids": ["good", "bad"],
        }
    )
    monkeypatch.setattr(inbox.importer, "validate_public_url", lambda _url: None)
    resolution_calls: list[list[str]] = []

    async def fixture_capture(_url: str) -> CaptureManifestV2:
        return manifest

    async def fixture_resolved(
        _url: str,
        _manifest: CaptureManifestV2,
        selected_ids: list[str],
        _context: object,
    ) -> list[ResolvedAsset]:
        resolution_calls.append(selected_ids)
        return [
            ResolvedAsset(
                manifest=manifest,
                source_selection_id=file_id,
                source_file_id=file_id,
                source_filename=f"{file_id}.gcode",
                download_url=f"https://fixture.invalid/{file_id}.gcode",
                source_item_id="3161",
            )
            for file_id in selected_ids
        ]

    async def fixture_stage(resolved: ResolvedAsset) -> list[StagedAsset]:
        staged = _stage_fixture_asset(tmp_path, manifest)
        staged_path = staged.staged_path.with_name(
            f"{tmp_path.name}-{resolved.source_selection_id}.gcode"
        )
        shutil.copyfile(staged.staged_path, staged_path)
        if resolved.source_selection_id == "bad":
            staged_path.write_bytes(staged_path.read_bytes() + b"\n; bad fixture\n")
        return [
            StagedAsset(
                resolved=resolved,
                staged_path=staged_path,
                result_key="self",
                blob_sha256=sha256_file(staged_path),
            )
        ]

    original_ingest = inbox.importer.ingest_orca_gcode
    fail_bad = True

    def one_bad_file(*args, **kwargs) -> None:
        nonlocal fail_bad
        if kwargs["original_filename"] == "bad.gcode" and fail_bad:
            fail_bad = False
            raise RuntimeError("fixture_child_failure")
        return original_ingest(*args, **kwargs)

    monkeypatch.setattr(
        inbox.import_resolvers, "resolve_capture_manifest", fixture_capture
    )
    monkeypatch.setattr(
        inbox.import_resolvers, "resolve_selected_assets", fixture_resolved
    )
    monkeypatch.setattr(inbox, "_download_resolved_asset", fixture_stage)
    monkeypatch.setattr(inbox.importer, "ingest_orca_gcode", one_bad_file)

    captured = client.post(
        "/api/v1/inbox",
        headers=auth_headers,
        json={"url": "https://www.printables.com/model/3161-3d-benchy"},
    )
    item_id = captured.json()["id"]
    first = client.post(
        f"/api/v1/inbox/{item_id}/import",
        headers=auth_headers,
        json={"selected_ids": ["good", "bad"]},
    )
    assert first.status_code == 200, first.text
    partial = client.get(f"/api/v1/inbox/{item_id}", headers=auth_headers).json()
    assert partial["state"] == "completed"
    assert partial["completion"] == "partial"
    assert {result["state"] for result in partial["results"]} == {
        "imported",
        "failed",
    }
    assert len(db_session.exec(select(File)).all()) == 1

    retried = client.post(f"/api/v1/inbox/{item_id}/retry", headers=auth_headers)
    assert retried.status_code == 200, retried.text
    assert retried.json()["manifest"]["selected_ids"] == ["bad"]
    assert resolution_calls == [["good", "bad"], ["bad"]]
    completed = client.get(f"/api/v1/inbox/{item_id}", headers=auth_headers).json()
    assert completed["state"] == "completed"
    assert completed["completion"] == "complete"
    assert {result["state"] for result in completed["results"]} == {"imported"}
    assert len(db_session.exec(select(File)).all()) == 2


@pytest.mark.parametrize(
    ("page_url", "title"),
    [
        ("https://www.printables.com/model/3161-3d-benchy/files", "3DBenchy"),
        ("https://www.thingiverse.com/thing:763622/files", "Whistle"),
        ("https://cdn.example.com/models/calibration-cube.stl", "Calibration cube"),
    ],
)
def test_named_api_key_captures_supported_browser_source_for_pending_imports(
    page_url: str,
    title: str,
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del auth_headers
    owner = db_session.exec(select(User).where(User.username == "test-writer")).one()
    _record, raw_key = create_api_key(db_session, owner.id, "Chrome importer")
    monkeypatch.setattr(inbox.importer, "validate_public_url", lambda _url: None)

    async def defer_resolution(_item_id: int) -> None:
        return None

    monkeypatch.setattr(inbox, "resolve", defer_resolution)
    login = client.post(
        "/api/v1/auth/login",
        json={"username": owner.username, "api_key": raw_key, "remember_me": False},
    )
    assert login.status_code == 200, login.text
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    captured = client.post(
        "/api/v1/inbox",
        headers=headers,
        json={"url": page_url, "title": title, "source_kind": "browser"},
    )

    assert captured.status_code == 202, captured.text
    assert captured.json()["source_kind"] == "browser"
    listed = client.get("/api/v1/inbox", headers=headers).json()
    assert [(row["source_url"], row["display_title"]) for row in listed] == [
        (page_url, title)
    ]
    assert db_session.exec(select(InboxItem)).one().owner_user_id == owner.id
