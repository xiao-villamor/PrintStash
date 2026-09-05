"""E2E: maintenance GC never treats a mounted folder as disposable storage."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlmodel import select

from app.core.config import settings
from app.db.models import File
from app.services import artifact_content, external_library
from app.services.library_source import SourceContent, SourceEntry, SourcePage
from tests.paths import FIXTURES_DIR


class _RemoteSource:
    def __init__(self, path: Path) -> None:
        self.path = path

    def list_page(self, prefix: str, *, cursor: str | None, limit: int) -> SourcePage:
        assert prefix == "models"
        assert cursor is None
        assert limit == 1000
        return SourcePage(
            entries=(
                SourceEntry(
                    key="models/sample.gcode",
                    size=self.path.stat().st_size,
                ),
            ),
            next_cursor=None,
            complete=True,
            metadata_ops=1,
        )

    @contextmanager
    def materialize(self, key: str, *, expected=None):
        assert key == "models/sample.gcode"
        yield SourceContent(self.path, SourceEntry(key, self.path.stat().st_size))


class TestAdminGc:
    @pytest.mark.asyncio
    async def test_remote_library_remains_read_only_end_to_end(
        self,
        api,
        superuser_headers,
        e2e_db,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        source_path = tmp_path / "remote-sample.gcode"
        source_path.write_bytes((FIXTURES_DIR / "sample.gcode").read_bytes())
        source_bytes = source_path.read_bytes()
        source = _RemoteSource(source_path)
        monkeypatch.setattr(external_library, "source_for_library", lambda _lib: source)
        monkeypatch.setattr(
            artifact_content,
            "source_for_file",
            lambda file_row: (source, file_row.source_key),
        )

        enabled = await api.put(
            "/api/v1/config",
            headers=superuser_headers,
            json={"external_libraries_enabled": True},
        )
        assert enabled.status_code == 200, enabled.text
        connection = await api.post(
            "/api/v1/storage-connections",
            headers=superuser_headers,
            json={
                "name": "e2e-read-only-s3",
                "kind": "s3",
                "configuration": {
                    "provider": "s3_self_hosted",
                    "bucket": "library",
                    "endpoint_url": "http://source.invalid",
                    "root": "library-root",
                    "addressing_style": "path",
                },
                "secrets": {"access_key": "access", "secret_key": "secret"},
            },
        )
        assert connection.status_code == 201, connection.text
        library = await api.post(
            "/api/v1/libraries",
            headers=superuser_headers,
            json={
                "name": "e2e remote library",
                "source_kind": "s3",
                "connection_id": connection.json()["id"],
                "source_prefix": "models",
                "scan_schedule": "",
            },
        )
        assert library.status_code == 201, library.text
        assert library.json()["writeback_enabled"] is False

        scan = await api.post(
            f"/api/v1/libraries/{library.json()['id']}/scan",
            headers=superuser_headers,
        )
        assert scan.status_code == 202, scan.text
        e2e_db.expire_all()
        file_row = e2e_db.exec(
            select(File).where(File.source_key == "models/sample.gcode")
        ).one()

        downloaded = await api.get(
            f"/api/v1/files/{file_row.id}/download", headers=superuser_headers
        )

        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == source_bytes
        assert source_path.read_bytes() == source_bytes

    @pytest.mark.asyncio
    async def test_admin_gc_preserves_preexisting_files_in_configured_data_dir(
        self, api, superuser_headers, e2e_db
    ) -> None:
        del e2e_db  # fixture selects the real on-disk DB and isolated storage paths
        library_file = Path(settings.data_dir) / "Jonathan" / "3D Prints" / "part.stl"
        library_file.parent.mkdir(parents=True, exist_ok=True)
        library_file.write_bytes(b"user-managed model")

        response = await api.post("/api/v1/admin/gc", headers=superuser_headers)

        assert response.status_code == 200, response.text
        assert response.json()["orphan_blobs"] == 0
        assert library_file.read_bytes() == b"user-managed model"
