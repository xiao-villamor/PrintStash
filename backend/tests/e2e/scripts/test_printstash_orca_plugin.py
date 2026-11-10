"""Native Orca callbacks upload source revisions through the real PrintStash API.

The Orca C++ binding is an external-host fake; HTTP, authentication, ingestion,
SQLite, storage and work execution remain real, including replay and rejection.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlmodel import select

from app.db.models import File, FileType
from app.db.session import get_session_factory, override_session_factory
from app.main import app
from tests.e2e._jobs import settle
from tests.factories import build_file, build_model
from tests.fakes.orca_host import export_context, plugin_host
from tests.fakes.server import start_server
from tests.paths import FIXTURES_DIR


@pytest_asyncio.fixture
async def native_export(api, superuser_headers, e2e_db, tmp_path):
    source = build_model(e2e_db, "3DBenchy")
    build_file(e2e_db, source, file_type=FileType.STL, filename="3dbenchy.stl")
    key = await api.post(
        "/api/v1/auth/api-keys", headers=superuser_headers, json={"name": "Orca test"}
    )
    assert key.status_code == 200, key.text
    path = tmp_path / "working.gcode.pp"
    path.write_bytes((FIXTURES_DIR / "real_orca_ender3_benchy.gcode").read_bytes())
    factory = get_session_factory()

    async def app_with_test_database(scope, receive, send):
        # Uvicorn owns another thread: bind the real E2E SQLite factory in
        # each request context, as ASGITransport does in the parent.
        previous = get_session_factory()
        override_session_factory(factory)
        try:
            await app(scope, receive, send)
        finally:
            override_session_factory(previous)

    server = start_server(app_with_test_database)
    try:
        with plugin_host() as host:
            capability = host.capabilities[0]()
            config = {
                "enabled": True,
                "url": server.base_url,
                "username": "e2e-admin",
                "api_key": key.json()["api_key"],
                "revision_label": "Plugin export",
                "revision_notes": "Test print",
                "revision_status": "needs_test",
                "recommended": False,
            }
            capability.save_config(json.dumps(config))
            yield SimpleNamespace(
                capability=capability,
                config=config,
                path=path,
                ctx=export_context(path, output_name=r"C:\exports\benchy-final.gcode"),
                model_id=source.id,
                headers=superuser_headers,
            )
    finally:
        server.stop()


@pytest.mark.asyncio
class TestNativeExport:
    async def test_uploads_revision_to_exact_source(self, native_export, api):
        result = native_export.capability.execute(native_export.ctx)
        settle()

        detail = await api.get(
            f"/api/v1/models/{native_export.model_id}", headers=native_export.headers
        )
        assert result.status == "success", result.message
        assert detail.status_code == 200, detail.text
        revisions = [
            row for row in detail.json()["files"] if row["file_type"] == "gcode"
        ]
        assert len(revisions) == 1
        assert revisions[0]["original_filename"] == "benchy-final.gcode"
        assert revisions[0]["revision_label"] == "Plugin export"
        assert revisions[0]["revision_notes"] == "Test print"
        assert revisions[0]["revision_status"] == "needs_test"
        assert (
            revisions[0]["metadata"]["native_context"]["source"]["filename"]
            == "3dbenchy.stl"
        )

    async def test_deduplicates_repeated_export(self, native_export, e2e_db):
        first = native_export.capability.execute(native_export.ctx)
        settle()
        second = native_export.capability.execute(native_export.ctx)
        settle()

        revisions = e2e_db.exec(
            select(File).where(File.file_type == FileType.GCODE)
        ).all()
        assert first.status == second.status == "success"
        assert len(revisions) == 1

    async def test_preserves_export_bytes(self, native_export):
        before = native_export.path.read_bytes()

        result = native_export.capability.execute(native_export.ctx)

        assert result.status == "success", result.message
        assert native_export.path.read_bytes() == before

    async def test_refuses_multi_object_in_strict_mode(self, native_export, e2e_db):
        native_export.path.write_text(
            "; printing object 3dbenchy.stl id:0 copy 0\n"
            "; printing object bracket.stl id:1 copy 0\nG28\n"
        )
        native_export.capability.save_config(
            json.dumps(native_export.config | {"strict_mapping": True})
        )

        result = native_export.capability.execute(native_export.ctx)
        settle()

        assert result.status == "skipped"
        assert "Upload failed" in result.message
        assert (
            e2e_db.exec(select(File).where(File.file_type == FileType.GCODE)).all()
            == []
        )
