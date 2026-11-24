"""Plugin metadata belongs to the export, never another plate's environment.

Only outbound HTTP is adapted to TestClient; the real ingestion route validates
and persists the submitted context before the test reads the resulting Revision.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from sqlmodel import select

from app.db.models import File, FileType, Metadata
from app.modules.identity.auth import create_api_key
from tests._env import use_local_storage
from tests.fakes.orca_host import export_context, plugin_host
from tests.integration.api.v1._ingest_assertions import drain_work


@pytest.fixture
def native_plugin(
    client, db_session, make_user, make_model, make_file, monkeypatch, tmp_path
):
    use_local_storage(tmp_path)
    user = make_user(superuser=True)
    _, key = create_api_key(db_session, user.id, "Native plugin test")
    source = make_model("Exported source")
    make_file(source, file_type=FileType.STL, filename="exported.stl")
    path = tmp_path / "working.pp"
    path.write_text("; printing object exported.stl id:0 copy 0\nG28\n")

    class Reply:
        def __init__(self, response):
            self.response = response
            self.status = response.status_code

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return self.response.content

    def request(req, **kwargs):
        response = client.post(
            req.full_url, content=req.data, headers=dict(req.header_items())
        )
        if response.is_error:
            raise HTTPError(req.full_url, response.status_code, "API error", {}, None)
        return Reply(response)

    with plugin_host() as host:
        monkeypatch.setattr(host.upload.urlrequest, "urlopen", request)
        capability = host.capabilities[0]()
        capability.save_config(
            json.dumps(
                {
                    "enabled": True,
                    "url": "http://testserver",
                    "username": user.username,
                    "api_key": key,
                }
            )
        )
        yield SimpleNamespace(capability=capability, path=path, source=source)


class TestPrintStashUpload:
    def test_ignores_stale_source_environment(
        self, native_plugin, db_session, monkeypatch
    ):
        monkeypatch.setenv("SLIC3R_INPUT_FILENAME", "another-plate.stl")
        monkeypatch.setenv("SLIC3R_NUM_OBJECTS", "17")
        monkeypatch.setenv("SLIC3R_PP_OUTPUT_NAME", "another-plate.gcode")

        result = native_plugin.capability.execute(
            export_context(native_plugin.path, output_name="actual.gcode")
        )
        drain_work()

        revision = db_session.exec(
            select(File).where(
                File.file_type == FileType.GCODE,
                File.model_id == native_plugin.source.id,
            )
        ).one()
        assert result.status == "success", result.message
        assert revision.model_id == native_plugin.source.id
        assert revision.original_filename == "actual.gcode"

    def test_uses_resolved_export_settings(
        self, native_plugin, db_session, monkeypatch
    ):
        monkeypatch.setenv("SLIC3R_PRINTER_MODEL", "Wrong printer")
        context = export_context(
            native_plugin.path,
            config={
                "printer_model": "Export printer",
                "print_settings_id": "Export process",
                "filament_type": "PLA;PETG",
                "filament_settings_id": "First;Second",
            },
        )

        result = native_plugin.capability.execute(context)
        drain_work()

        revision = db_session.exec(
            select(File).where(
                File.file_type == FileType.GCODE,
                File.model_id == native_plugin.source.id,
            )
        ).one()
        metadata = db_session.exec(
            select(Metadata).where(Metadata.file_id == revision.id)
        ).one()
        assert result.status == "success", result.message
        captured = json.loads(metadata.native_context_json)
        assert captured["printer"]["model"] == "Export printer"
        assert captured["process"]["preset"] == "Export process"
        assert captured["filaments"] == [
            {"type": "PLA", "preset": "First"},
            {"type": "PETG", "preset": "Second"},
        ]
