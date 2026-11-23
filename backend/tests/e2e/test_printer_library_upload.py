"""A saved Revision reaches a disposable Moonraker through the upload-only API."""

import pytest

from app.modules.storage.storage_backend.runtime import get_backend
from tests.factories import a_gcode_artifact, build_printer
from tests.fakes.mock_printer import create_app
from tests.fakes.server import start_server


class TestUploadFromLibrary:
    @pytest.mark.asyncio
    async def test_upload_only_exposes_the_confirmed_file_without_starting_the_printer(
        self, api, superuser_headers, e2e_db
    ) -> None:
        controller, state = create_app()
        running = start_server(controller)
        try:
            printer = build_printer(e2e_db, moonraker_url=running.base_url)
            revision = a_gcode_artifact(e2e_db, "transfer-only")
            get_backend().write_bytes(b"G28\n", revision.path)
            path = f"/api/v1/printers/{printer.id}"

            sent = await api.post(
                f"{path}/send",
                headers=superuser_headers,
                json={"file_id": revision.id, "start_print": False},
            )
            assert sent.status_code == 200, sent.text
            receipt = sent.json()
            assert receipt["state"] == "completed"
            assert receipt["started_at"] is None
            assert state.file_contents[receipt["remote_filename"]] == b"G28\n"
            assert not state.sim.is_active()

            files = await api.get(f"{path}/files", headers=superuser_headers)
            assert files.status_code == 200, files.text
            assert files.json()[0]["remote_filename"] == receipt["remote_filename"]
            assert files.json()[0]["matched_by"] == "upload_history"
        finally:
            running.stop()
