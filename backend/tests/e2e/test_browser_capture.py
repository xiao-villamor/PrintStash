"""E2E: the Chrome extension's API contract persists a browser capture."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from printstash_core.imports import CaptureManifestV2, ResolvedAsset, StagedAsset
from sqlmodel import select

from app.core.config import settings
from app.db.models import (
    ArtifactProvenanceLink,
    File,
    InboxItem,
    InboxItemState,
    Model,
    User,
)
from app.services import import_resolvers, inbox
from app.services.auth import create_api_key
from app.services.hashing import sha256_file
from tests.paths import FIXTURES_DIR


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
    source = FIXTURES_DIR / "sample.gcode"
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


class TestBrowserCapture:
    """The browser extension's whole capture flow, end to end against fixtures.

    A capture starts in a page the server never sees: the extension reads the
    model page, uploads the files one at a time, and PrintStash assembles them
    into an inbox item the user reviews. Every step crosses a trust boundary — the
    payload is attacker-shaped by construction — so these run the real flow
    rather than the services under it."""

    @pytest.mark.asyncio
    async def test_browser_capture_resolves_offline_printables_fixture_to_review(
        self, api, superuser_headers, e2e_db, monkeypatch
    ) -> None:
        """The public capture endpoint persists V2 review data without egress."""
        owner = e2e_db.exec(select(User).where(User.username == "e2e-admin")).one()
        monkeypatch.setattr(inbox.importer, "validate_public_url", lambda _url: None)

        async def _fixture_capture(_url: str) -> CaptureManifestV2:
            return _captured_manifest()

        monkeypatch.setattr(
            inbox.import_resolvers, "resolve_capture_manifest", _fixture_capture
        )
        captured = await api.post(
            "/api/v1/inbox",
            headers=superuser_headers,
            json={"url": "https://www.printables.com/model/3161-3d-benchy"},
        )

        assert captured.status_code == 202, captured.text
        detail = await api.get(
            f"/api/v1/inbox/{captured.json()['id']}", headers=superuser_headers
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["state"] == "review"
        assert detail.json()["manifest"] == _captured_manifest().to_dict()
        assert e2e_db.exec(select(InboxItem)).one().owner_user_id == owner.id

    @pytest.mark.asyncio
    async def test_offline_capture_import_recapture_deduplicates_durable_artifact(
        self, api, superuser_headers, e2e_db, monkeypatch, tmp_path
    ) -> None:
        """Offline URL capture follows the real Inbox/import/provenance transaction."""
        manifest = _captured_manifest()
        manifest = CaptureManifestV2.from_dict(
            {
                **manifest.to_dict(),
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

        async def _fixture_capture(_url: str) -> CaptureManifestV2:
            return manifest

        # `context` is required, not defaulted: it carries the owner the resolution
        # runs as, and a stand-in that quietly tolerated its absence is what let this
        # signature drift out of step with production in the first place.
        async def _fixture_resolved(
            _url: str,
            _manifest: CaptureManifestV2,
            _ids: list[str],
            context: import_resolvers.ProviderResolutionContext,
        ):
            assert context.owner_user_id is not None
            return [_stage_fixture_asset(tmp_path, manifest).resolved]

        async def _fixture_stage(resolved: ResolvedAsset) -> list[StagedAsset]:
            return [_stage_fixture_asset(tmp_path, resolved.manifest)]

        monkeypatch.setattr(
            inbox.import_resolvers, "resolve_capture_manifest", _fixture_capture
        )
        monkeypatch.setattr(
            inbox.import_resolvers, "resolve_selected_assets", _fixture_resolved
        )
        monkeypatch.setattr(inbox, "_download_resolved_asset", _fixture_stage)

        async def capture_and_import() -> dict:
            captured = await api.post(
                "/api/v1/inbox",
                headers=superuser_headers,
                json={"url": "https://www.printables.com/model/3161-3d-benchy"},
            )
            assert captured.status_code == 202, captured.text
            item_id = captured.json()["id"]
            imported = await api.post(
                f"/api/v1/inbox/{item_id}/import",
                headers=superuser_headers,
                json={"selected_ids": ["stl-1"]},
            )
            assert imported.status_code == 200, imported.text
            return (
                await api.get(f"/api/v1/inbox/{item_id}", headers=superuser_headers)
            ).json()

        first = await capture_and_import()
        assert first["state"] == "completed", first.get("error_code")
        assert first["results"][0]["state"] == "imported"
        assert len(e2e_db.exec(select(File)).all()) == 1
        assert len(e2e_db.exec(select(ArtifactProvenanceLink)).all()) == 1
        assert len(e2e_db.exec(select(Model)).all()) == 1

        model_id = first["results"][0]["model_id"]
        source = (
            await api.get(
                f"/api/v1/models/{model_id}/provenance", headers=superuser_headers
            )
        ).json()["sources"][0]
        overridden = await api.patch(
            f"/api/v1/models/{model_id}/provenance/{source['id']}",
            headers=superuser_headers,
            json={"overrides": {"title": "My Benchy"}},
        )
        assert overridden.status_code == 200, overridden.text
        title = overridden.json()["sources"][0]["fields"][0]
        assert title["effective_value"] == "My Benchy"
        assert title["effective_origin"] == "user"

        second = await capture_and_import()
        assert second["state"] == "completed"
        assert second["results"][0]["state"] == "deduplicated"
        assert len(e2e_db.exec(select(File)).all()) == 1
        preserved = await api.get(
            f"/api/v1/models/{model_id}/provenance", headers=superuser_headers
        )
        assert (
            preserved.json()["sources"][0]["fields"][0]["effective_value"]
            == "My Benchy"
        )

    @pytest.mark.asyncio
    async def test_offline_capture_partial_result_retries_only_failed_selection(
        self, api, superuser_headers, e2e_db, monkeypatch, tmp_path
    ) -> None:
        """One child can fail while its sibling remains durable and is not retried."""
        manifest = CaptureManifestV2.from_dict(
            {
                **_captured_manifest().to_dict(),
                "files": [
                    {
                        "id": "good",
                        "name": "good.gcode",
                        "file_type": "gcode",
                        "size": 1,
                    },
                    {"id": "bad", "name": "bad.gcode", "file_type": "gcode", "size": 1},
                ],
                "selected_ids": ["good", "bad"],
            }
        )
        monkeypatch.setattr(inbox.importer, "validate_public_url", lambda _url: None)
        resolution_calls: list[list[str]] = []

        async def _fixture_capture(_url: str) -> CaptureManifestV2:
            return manifest

        async def _fixture_resolved(
            _url: str,
            _manifest: CaptureManifestV2,
            selected_ids: list[str],
            context: import_resolvers.ProviderResolutionContext,
        ) -> list[ResolvedAsset]:
            assert context.owner_user_id is not None
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

        async def _fixture_stage(resolved: ResolvedAsset) -> list[StagedAsset]:
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

        def _one_bad_file(*args, **kwargs) -> None:
            nonlocal fail_bad
            if kwargs["original_filename"] == "bad.gcode" and fail_bad:
                fail_bad = False
                raise RuntimeError("fixture_child_failure")
            original_ingest(*args, **kwargs)

        monkeypatch.setattr(
            inbox.import_resolvers, "resolve_capture_manifest", _fixture_capture
        )
        monkeypatch.setattr(
            inbox.import_resolvers, "resolve_selected_assets", _fixture_resolved
        )
        monkeypatch.setattr(inbox, "_download_resolved_asset", _fixture_stage)
        monkeypatch.setattr(inbox.importer, "ingest_orca_gcode", _one_bad_file)

        captured = await api.post(
            "/api/v1/inbox",
            headers=superuser_headers,
            json={"url": "https://www.printables.com/model/3161-3d-benchy"},
        )
        item_id = captured.json()["id"]
        first = await api.post(
            f"/api/v1/inbox/{item_id}/import",
            headers=superuser_headers,
            json={"selected_ids": ["good", "bad"]},
        )
        assert first.status_code == 200, first.text
        partial = (
            await api.get(f"/api/v1/inbox/{item_id}", headers=superuser_headers)
        ).json()
        assert partial["state"] == "completed"
        assert partial["completion"] == "partial"
        assert {result["state"] for result in partial["results"]} == {
            "imported",
            "failed",
        }
        assert len(e2e_db.exec(select(File)).all()) == 1

        retried = await api.post(
            f"/api/v1/inbox/{item_id}/retry", headers=superuser_headers
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["manifest"]["selected_ids"] == ["bad"]
        assert resolution_calls == [["good", "bad"], ["bad"]]
        completed = (
            await api.get(f"/api/v1/inbox/{item_id}", headers=superuser_headers)
        ).json()
        assert completed["state"] == "completed"
        assert completed["completion"] == "complete"
        assert {result["state"] for result in completed["results"]} == {"imported"}
        assert len(e2e_db.exec(select(File)).all()) == 2

    @pytest.mark.asyncio
    async def test_named_api_key_verifies_browser_extension_connection(
        self, api, superuser_headers, e2e_db
    ) -> None:
        del superuser_headers
        owner = e2e_db.exec(select(User).where(User.username == "e2e-admin")).one()
        _record, raw_key = create_api_key(e2e_db, owner.id, "Browser connection")

        health = await api.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok", "name": "PrintStash"}

        login = await api.post(
            "/api/v1/auth/login",
            json={"username": owner.username, "api_key": raw_key, "remember_me": False},
        )
        assert login.status_code == 200, login.text
        profile = await api.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        assert profile.status_code == 200, profile.text
        assert profile.json()["username"] == owner.username
        assert profile.json()["is_superuser"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("page_url", "title"),
        [
            ("https://www.printables.com/model/3161-3d-benchy/files", "3DBenchy"),
            ("https://www.thingiverse.com/thing:763622/files", "Whistle"),
            ("https://cdn.example.com/models/calibration-cube.stl", "Calibration cube"),
        ],
    )
    async def test_named_api_key_captures_supported_browser_source_for_pending_imports(
        self, api, superuser_headers, e2e_db, monkeypatch, page_url: str, title: str
    ) -> None:
        del superuser_headers  # seeds the same account the extension logs in as
        owner = e2e_db.exec(select(User).where(User.username == "e2e-admin")).one()
        _record, raw_key = create_api_key(e2e_db, owner.id, "Chrome importer")

        # Capture durability is the extension boundary. Source resolution has its
        # own real-egress E2E coverage and is deliberately deferred here.
        monkeypatch.setattr(inbox.importer, "validate_public_url", lambda _url: None)

        async def _defer_resolution(_item_id: int) -> None:
            return None

        monkeypatch.setattr(inbox, "resolve", _defer_resolution)

        login = await api.post(
            "/api/v1/auth/login",
            json={"username": owner.username, "api_key": raw_key, "remember_me": False},
        )
        assert login.status_code == 200, login.text
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        captured = await api.post(
            "/api/v1/inbox",
            headers=headers,
            json={
                "url": page_url,
                "title": title,
                "source_kind": "browser",
            },
        )

        assert captured.status_code == 202, captured.text
        assert captured.json()["source_kind"] == "browser"
        listed = (await api.get("/api/v1/inbox", headers=headers)).json()
        assert [(row["source_url"], row["display_title"]) for row in listed] == [
            (page_url, title)
        ]
        assert e2e_db.exec(select(InboxItem)).one().owner_user_id == owner.id

    @pytest.mark.asyncio
    async def test_named_api_key_stages_makerworld_package_from_browser(
        self, api, superuser_headers, e2e_db
    ) -> None:
        del superuser_headers
        owner = e2e_db.exec(select(User).where(User.username == "e2e-admin")).one()
        _record, raw_key = create_api_key(
            e2e_db, owner.id, "Chrome MakerWorld importer"
        )
        login = await api.post(
            "/api/v1/auth/login",
            json={"username": owner.username, "api_key": raw_key, "remember_me": False},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        page_url = "https://makerworld.com/en/models/1234-widget"

        captured = await api.post(
            "/api/v1/inbox/browser-upload",
            headers=headers,
            data={"source_url": page_url, "title": "Widget"},
            files={
                "file": (
                    "widget.3mf",
                    b"browser-owned-package",
                    "application/octet-stream",
                )
            },
        )

        assert captured.status_code == 201, captured.text
        body = captured.json()
        assert body["state"] == "review"
        assert body["source_kind"] == "browser"
        assert body["source_url"] == page_url
        assert body["manifest"] == {
            "kind": "browser_file",
            "title": "widget.3mf",
            "filename": "widget.3mf",
            "size": len(b"browser-owned-package"),
        }
        e2e_db.expire_all()
        row = e2e_db.exec(select(InboxItem)).one()
        assert row.state == InboxItemState.REVIEW
        assert row.staging_key is not None
        staged = Path(row.staging_key)
        assert staged.read_bytes() == b"browser-owned-package"

    @pytest.mark.asyncio
    async def test_browser_upload_rejects_non_makerworld_source(
        self, api, superuser_headers, e2e_db
    ) -> None:
        del superuser_headers
        owner = e2e_db.exec(select(User).where(User.username == "e2e-admin")).one()
        _record, raw_key = create_api_key(e2e_db, owner.id, "Chrome source check")
        login = await api.post(
            "/api/v1/auth/login",
            json={"username": owner.username, "api_key": raw_key, "remember_me": False},
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        captured = await api.post(
            "/api/v1/inbox/browser-upload",
            headers=headers,
            data={"source_url": "https://www.printables.com/model/3161-benchy"},
            files={"file": ("benchy.3mf", b"not-staged", "application/octet-stream")},
        )

        assert captured.status_code == 400
        assert captured.json()["detail"] == "makerworld_model_page_required"
        assert e2e_db.exec(select(InboxItem)).all() == []
