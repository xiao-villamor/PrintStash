"""Pulling the G-code a slicer embedded inside a 3MF back out again.

A Bambu- or Orca-produced 3MF carries one G-code per plate, and the viewer wants exactly
one of them as text. Everything that can go wrong with that is a *lookup* failure rather
than a server fault, and each one has its own code so the UI can say something true: no
such plate, more than one and no plate named, not a 3MF at all, not a zip, or a member
that decompresses to more than the configured cap — the last being the zip-bomb guard,
which must trip before the bytes are materialised.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from sqlmodel import Session

from app.core.config import settings
from app.db.models import File, FileType
from app.services.storage_backend import get_backend
from tests.factories import build_file, build_model

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def api_client() -> AsyncIterator[httpx.AsyncClient]:
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as client:
        yield client


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return data.getvalue()


@pytest.fixture
def three_mf(db_session: Session, tmp_path: Path):
    """Store a 3MF (or any type) on disk with the row that points at it."""
    made = {"n": 0}

    def build(
        *,
        content: bytes | None = None,
        file_type: FileType = FileType.THREE_MF,
    ) -> File:
        made["n"] += 1
        directory = tmp_path / f"artifact-{made['n']}"
        directory.mkdir(parents=True, exist_ok=True)
        slug = f"preview-{file_type.value}-{made['n']}"
        model = build_model(
            db_session, name=slug, slug=slug, hash=(slug.encode().hex() * 64)[:64]
        )
        path = directory / f"{slug}.3mf"
        if content is not None:
            path.write_bytes(content)
        artifact = build_file(
            db_session,
            model,
            path=str(path),
            filename=path.name,
            file_type=file_type,
            version=1,
            size_bytes=len(content or b""),
            sha256="a" * 64,
        )
        db_session.close()
        return artifact

    return build


class TestEmbeddedGcode:
    async def test_serves_the_named_plate(
        self, api_client: httpx.AsyncClient, auth_headers: dict[str, str], three_mf
    ) -> None:
        artifact = three_mf(
            content=_zip_bytes(
                {
                    "Metadata/plate_1.gcode": b"wrong\n",
                    "Metadata/plate_2.gcode": b"expected\n",
                }
            )
        )

        response = await api_client.get(
            f"/api/v1/files/{artifact.id}/embedded-gcode?plate_index=2",
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert response.content == b"expected\n"

    async def test_serves_it_as_inline_text(
        self, api_client: httpx.AsyncClient, auth_headers: dict[str, str], three_mf
    ) -> None:
        artifact = three_mf(
            content=_zip_bytes({"Metadata/plate_2.gcode": b"expected\n"})
        )

        response = await api_client.get(
            f"/api/v1/files/{artifact.id}/embedded-gcode?plate_index=2",
            headers=auth_headers,
        )

        assert response.headers["content-type"].startswith("text/plain")
        assert response.headers["content-disposition"] == (
            'inline; filename="plate_2.gcode"'
        )

    async def test_falls_back_to_the_only_plate_when_none_is_named(
        self, api_client: httpx.AsyncClient, auth_headers: dict[str, str], three_mf
    ) -> None:
        artifact = three_mf(
            content=_zip_bytes({"Metadata/plate_9.gcode": b"fallback\n"})
        )

        response = await api_client.get(
            f"/api/v1/files/{artifact.id}/embedded-gcode", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert response.content == b"fallback\n"

    async def test_reports_a_plate_that_is_not_there(
        self, api_client: httpx.AsyncClient, auth_headers: dict[str, str], three_mf
    ) -> None:
        artifact = three_mf(content=_zip_bytes({"Metadata/plate_1.gcode": b"one\n"}))

        response = await api_client.get(
            f"/api/v1/files/{artifact.id}/embedded-gcode?plate_index=4",
            headers=auth_headers,
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "embedded_gcode_not_found"

    async def test_refuses_to_guess_between_plates(
        self, api_client: httpx.AsyncClient, auth_headers: dict[str, str], three_mf
    ) -> None:
        artifact = three_mf(
            content=_zip_bytes(
                {
                    "Metadata/plate_1.gcode": b"one\n",
                    "Metadata/plate_2.gcode": b"two\n",
                }
            )
        )

        response = await api_client.get(
            f"/api/v1/files/{artifact.id}/embedded-gcode", headers=auth_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "embedded_gcode_ambiguous"

    async def test_rejects_a_file_that_cannot_embed_gcode(
        self, api_client: httpx.AsyncClient, auth_headers: dict[str, str], three_mf
    ) -> None:
        artifact = three_mf(content=b"G28\n", file_type=FileType.GCODE)

        response = await api_client.get(
            f"/api/v1/files/{artifact.id}/embedded-gcode", headers=auth_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "embedded_gcode_not_supported"

    async def test_reports_a_container_that_is_not_a_zip(
        self, api_client: httpx.AsyncClient, auth_headers: dict[str, str], three_mf
    ) -> None:
        artifact = three_mf(content=b"not zip")

        response = await api_client.get(
            f"/api/v1/files/{artifact.id}/embedded-gcode", headers=auth_headers
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "embedded_gcode_malformed"

    async def test_refuses_a_member_that_decompresses_past_the_cap(
        self,
        api_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        three_mf,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(get_backend(), "exists", lambda _key: True)
        monkeypatch.setattr(settings._frozen, "three_mf_preview_max_uncompressed_mb", 1)
        artifact = three_mf(
            content=_zip_bytes({"Metadata/plate_1.gcode": b"x" * (1024 * 1024 + 1)})
        )

        response = await api_client.get(
            f"/api/v1/files/{artifact.id}/embedded-gcode", headers=auth_headers
        )

        # The guard trips on the declared size, before the bytes are materialised.
        assert response.status_code == 413, response.text
        assert response.json()["detail"] == "embedded_gcode_too_large"

    async def test_reports_a_missing_blob_as_gone(
        self, api_client: httpx.AsyncClient, auth_headers: dict[str, str], three_mf
    ) -> None:
        artifact = three_mf(content=_zip_bytes({"Metadata/plate_1.gcode": b"one\n"}))
        Path(artifact.path).unlink()

        response = await api_client.get(
            f"/api/v1/files/{artifact.id}/embedded-gcode", headers=auth_headers
        )

        assert response.status_code == 410, response.text
        assert response.json()["detail"] == "file_blob_missing"

    async def test_reports_a_blob_that_vanishes_mid_read_as_gone(
        self,
        api_client: httpx.AsyncClient,
        auth_headers: dict[str, str],
        three_mf,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.api.v1 import files as files_api

        artifact = three_mf(content=_zip_bytes({"Metadata/plate_1.gcode": b"one\n"}))

        def vanished(*_args: object, **_kwargs: object):
            raise FileNotFoundError("deleted between the check and the read")

        monkeypatch.setattr(files_api, "read_embedded_gcode", vanished)

        response = await api_client.get(
            f"/api/v1/files/{artifact.id}/embedded-gcode", headers=auth_headers
        )

        assert response.status_code == 410, response.text
        assert response.json()["detail"] == "file_blob_missing"

    async def test_rejects_an_unauthenticated_caller(
        self, api_client: httpx.AsyncClient, three_mf
    ) -> None:
        artifact = three_mf(content=_zip_bytes({"Metadata/plate_1.gcode": b"one\n"}))

        response = await api_client.get(f"/api/v1/files/{artifact.id}/embedded-gcode")

        assert response.status_code == 401, response.text
