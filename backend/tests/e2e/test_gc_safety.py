"""E2E: maintenance GC never treats a mounted folder as disposable storage."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import settings


class TestAdminGc:
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
