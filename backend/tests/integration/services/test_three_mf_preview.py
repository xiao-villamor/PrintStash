"""Defends ``test_embedded_gcode_api_selects_plate_and_serves_inline`` behavior for the ``services`` production unit.

A failure means this boundary no longer preserves its observable contract.
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
from app.db.models import File, FileType, Model
from app.services.storage_backend import get_backend

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def api_client() -> AsyncIterator[httpx.AsyncClient]:
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://app") as client:
        yield client


def _model(db_session: Session, *, slug: str) -> Model:
    model = Model(name=slug, slug=slug, hash=(slug.encode().hex() * 64)[:64])
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    assert model.id is not None
    return model


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return data.getvalue()


def _file(
    db_session: Session,
    tmp_path: Path,
    *,
    file_type: FileType = FileType.THREE_MF,
    content: bytes | None = None,
) -> File:
    tmp_path.mkdir(parents=True, exist_ok=True)
    model = _model(
        db_session,
        slug=f"preview-{file_type.value}-{tmp_path.name}",
    )
    assert model.id is not None
    path = tmp_path / f"{model.slug}.3mf"
    if content is not None:
        path.write_bytes(content)
    artifact = File(
        model_id=model.id,
        path=str(path),
        original_filename=path.name,
        file_type=file_type,
        version=1,
        size_bytes=len(content or b""),
        sha256=("a" * 64),
    )
    db_session.add(artifact)
    db_session.commit()
    db_session.refresh(artifact)
    return artifact


async def test_embedded_gcode_api_selects_plate_and_serves_inline(
    api_client: httpx.AsyncClient,
    db_session: Session,
    auth_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    artifact = _file(
        db_session,
        tmp_path,
        content=_zip_bytes(
            {
                "Metadata/plate_1.gcode": b"wrong\n",
                "Metadata/plate_2.gcode": b"expected\n",
            }
        ),
    )
    db_session.close()

    response = await api_client.get(
        f"/api/v1/files/{artifact.id}/embedded-gcode?plate_index=2",
        headers=auth_headers,
    )

    assert response.status_code == 200, response.text
    assert response.content == b"expected\n"
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["content-disposition"] == (
        'inline; filename="plate_2.gcode"'
    )


async def test_embedded_gcode_api_uses_unique_fallback_without_query(
    api_client: httpx.AsyncClient,
    db_session: Session,
    auth_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    artifact = _file(
        db_session,
        tmp_path,
        content=_zip_bytes({"Metadata/plate_9.gcode": b"fallback\n"}),
    )
    db_session.close()

    response = await api_client.get(
        f"/api/v1/files/{artifact.id}/embedded-gcode", headers=auth_headers
    )

    assert response.status_code == 200
    assert response.content == b"fallback\n"


async def test_embedded_gcode_api_maps_lookup_errors_and_non_3mf(
    api_client: httpx.AsyncClient,
    db_session: Session,
    auth_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    missing = _file(
        db_session,
        tmp_path,
        content=_zip_bytes({"Metadata/plate_1.gcode": b"one\n"}),
    )
    db_session.close()
    response = await api_client.get(
        f"/api/v1/files/{missing.id}/embedded-gcode?plate_index=4",
        headers=auth_headers,
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "embedded_gcode_not_found"

    ambiguous = _file(
        db_session,
        tmp_path / "ambiguous",
        content=_zip_bytes(
            {
                "Metadata/plate_1.gcode": b"one\n",
                "Metadata/plate_2.gcode": b"two\n",
            }
        ),
    )
    db_session.close()
    response = await api_client.get(
        f"/api/v1/files/{ambiguous.id}/embedded-gcode", headers=auth_headers
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "embedded_gcode_ambiguous"

    non_3mf = _file(
        db_session,
        tmp_path / "gcode",
        file_type=FileType.GCODE,
        content=b"G28\n",
    )
    db_session.close()
    response = await api_client.get(
        f"/api/v1/files/{non_3mf.id}/embedded-gcode", headers=auth_headers
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "embedded_gcode_not_supported"


async def test_embedded_gcode_api_requires_access_and_reports_missing_blob(
    api_client: httpx.AsyncClient,
    db_session: Session,
    auth_headers: dict[str, str],
    tmp_path: Path,
) -> None:
    artifact = _file(
        db_session,
        tmp_path,
        content=_zip_bytes({"Metadata/plate_1.gcode": b"one\n"}),
    )
    db_session.close()
    unauthorized = await api_client.get(f"/api/v1/files/{artifact.id}/embedded-gcode")
    assert unauthorized.status_code == 401

    path = Path(artifact.path)
    path.unlink()
    db_session.close()
    missing = await api_client.get(
        f"/api/v1/files/{artifact.id}/embedded-gcode", headers=auth_headers
    )
    assert missing.status_code == 410
    assert missing.json()["detail"] == "file_blob_missing"


async def test_embedded_gcode_api_maps_malformed_and_bomb(
    api_client: httpx.AsyncClient,
    db_session: Session,
    auth_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch,
) -> None:
    malformed = _file(db_session, tmp_path, content=b"not zip")
    db_session.close()
    response = await api_client.get(
        f"/api/v1/files/{malformed.id}/embedded-gcode", headers=auth_headers
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "embedded_gcode_malformed"

    monkeypatch.setattr(get_backend(), "exists", lambda _key: True)
    monkeypatch.setattr(settings._frozen, "three_mf_preview_max_uncompressed_mb", 1)
    bomb = _file(
        db_session,
        tmp_path / "bomb",
        content=_zip_bytes({"Metadata/plate_1.gcode": b"x" * (1024 * 1024 + 1)}),
    )
    db_session.close()
    response = await api_client.get(
        f"/api/v1/files/{bomb.id}/embedded-gcode", headers=auth_headers
    )
    assert response.status_code == 413
    assert response.json()["detail"] == "embedded_gcode_too_large"
