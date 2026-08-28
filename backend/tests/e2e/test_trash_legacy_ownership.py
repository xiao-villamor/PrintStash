"""E2E: upgraded local Artifacts remain purgeable through the real API."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from app.core.time import utcnow
from app.db.models import FileType
from app.services.storage_backend import get_backend
from tests.factories import build_file, build_model


class TestTrash:
    @pytest.mark.asyncio
    async def test_trash_purges_a_pre_ledger_artifact_it_can_verify(
        self, api, superuser_headers, e2e_db
    ) -> None:
        backend = get_backend()
        content = b"pre-ledger artifact bytes"
        model = build_model(
            e2e_db,
            name="Legacy bracket",
            slug="legacy-bracket",
            hash=hashlib.sha256(b"legacy model").hexdigest(),
            deleted_at=utcnow(),
        )
        key = backend.blob_key(model.slug, 1, "bracket.stl")
        path = Path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)  # simulates bytes written before the ledger existed
        build_file(
            e2e_db,
            model,
            path=key,
            filename="bracket.stl",
            file_type=FileType.STL,
            version=1,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

        trash = (
            await api.get("/api/v1/models/trash", headers=superuser_headers)
        ).json()
        assert trash[0]["size_bytes"] == len(content)
        assert trash[0]["file_count"] == 1

        purged = await api.delete(
            f"/api/v1/models/{model.id}/purge", headers=superuser_headers
        )
        assert purged.status_code == 200, purged.text
        assert purged.json()["storage_completed"] == 1
        assert not path.exists()
        assert (
            await api.get("/api/v1/models/trash", headers=superuser_headers)
        ).json() == []
