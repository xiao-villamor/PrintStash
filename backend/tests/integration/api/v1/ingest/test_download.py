"""Importer downloads honor response, redirect, size, and SSRF contracts.

Integration transport responses keep these boundary outcomes deterministic without
placing a mocked egress seam in the contract tier.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterator
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import _overlay, settings
from app.core.url_safety import PinnedTarget
from app.db.models import File, FileType, Model
from app.services import importer
from tests.paths import REPO_ROOT

TESTDATA = REPO_ROOT / "testdata"
BENCHY_STL = TESTDATA / "benchy" / "3dbenchy.stl"
_REDIRECTS = {301, 302, 303, 307, 308}


@pytest.fixture(autouse=True)
def _use_file_backed_db(file_backed_integration_db: None) -> None:
    """Let request, worker, and assertion sessions use separate connections."""


def _requires(*paths: Path):
    missing = [path for path in paths if not path.exists()]
    return pytest.mark.skipif(
        bool(missing), reason=f"missing real fixture(s): {missing}"
    )


def _configure_storage(tmp_path: Path) -> None:
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"
    settings.incoming_dir.mkdir(parents=True, exist_ok=True)


def _job(client: TestClient, response, headers: dict[str, str]) -> dict:
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    job = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=headers)
    assert job.status_code == 200, job.text
    return job.json()


@pytest.fixture
def http_server() -> Iterator[tuple[str, dict[str, dict]]]:
    routes: dict[str, dict] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        spec = routes.get(request.url.path)
        if spec is None:
            return httpx.Response(404)
        status = spec.get("status", 200)
        body: bytes = spec.get("body", b"")
        headers = dict(spec.get("headers", {}))
        if status not in _REDIRECTS:
            headers["Content-Length"] = str(len(body))
        return httpx.Response(status, headers=headers, content=body)

    base = "http://fixture.invalid"

    def resolve(url: str) -> PinnedTarget:
        return PinnedTarget(
            url=url,
            host="fixture.invalid",
            port=80,
            ip="192.0.2.1",
        )

    with (
        patch("app.services.importer.resolve_public_target", side_effect=resolve),
        patch(
            "app.services.importer.pinned_transport",
            return_value=httpx.MockTransport(handler),
        ),
    ):
        yield base, routes


@_requires(BENCHY_STL)
@pytest.mark.asyncio
async def test_download_to_staging_fetches_real_file(
    tmp_path: Path, http_server: tuple[str, dict[str, dict]]
) -> None:
    _configure_storage(tmp_path)
    base, routes = http_server
    stl_bytes = BENCHY_STL.read_bytes()
    routes["/download"] = {
        "headers": {
            "Content-Type": "model/stl",
            "Content-Disposition": 'attachment; filename="3dbenchy.stl"',
        },
        "body": stl_bytes,
    }

    staged, filename = await importer.download_to_staging(f"{base}/download")

    assert filename == "3dbenchy.stl"
    assert staged.exists()
    assert staged.read_bytes() == stl_bytes
    assert staged.parent == settings.incoming_dir
    assert staged.suffix == ".stl"


@pytest.mark.asyncio
async def test_download_to_staging_follows_redirect(
    tmp_path: Path, http_server: tuple[str, dict[str, dict]]
) -> None:
    _configure_storage(tmp_path)
    base, routes = http_server
    routes["/start"] = {"status": 302, "headers": {"Location": "/final.stl"}}
    routes["/final.stl"] = {
        "headers": {"Content-Type": "model/stl"},
        "body": b"solid x\nendsolid x\n",
    }

    staged, filename = await importer.download_to_staging(f"{base}/start")

    # Filename falls back to the final URL's path component.
    assert filename == "final.stl"
    assert staged.read_bytes() == b"solid x\nendsolid x\n"


@pytest.mark.asyncio
async def test_download_to_staging_enforces_size_limit(
    tmp_path: Path, http_server: tuple[str, dict[str, dict]]
) -> None:
    _configure_storage(tmp_path)
    _overlay["max_upload_mb"] = 1  # 1 MiB cap
    base, routes = http_server
    routes["/big.stl"] = {"body": b"\0" * (2 * 1024 * 1024)}  # 2 MiB

    incoming_before = set(settings.incoming_dir.iterdir())
    with pytest.raises(importer.ImportError_) as exc:
        await importer.download_to_staging(f"{base}/big.stl")

    assert str(exc.value) == "download_too_large"
    # The oversized partial download was cleaned up, not left in staging.
    assert set(settings.incoming_dir.iterdir()) == incoming_before


@pytest.mark.asyncio
async def test_download_to_staging_rejects_private_host(
    tmp_path: Path,
) -> None:
    """Without relaxing the guard, the loopback server is refused (real SSRF)."""
    _configure_storage(tmp_path)

    with pytest.raises(importer.ImportError_) as exc:
        await importer.download_to_staging("http://127.0.0.1:9/download")
    assert str(exc.value) == "url_target_not_public"


@_requires(BENCHY_STL)
def test_ingest_url_downloads_and_ingests_for_real(
    tmp_path: Path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
    http_server: tuple[str, dict[str, dict]],
) -> None:
    _configure_storage(tmp_path)
    base, routes = http_server
    routes["/3dbenchy.stl"] = {
        "headers": {"Content-Type": "model/stl"},
        "body": BENCHY_STL.read_bytes(),
    }
    url = f"{base}/3dbenchy.stl"

    payload = _job(
        client,
        client.post(
            "/api/v1/ingest/url",
            headers=auth_headers,
            json={"url": url},
        ),
        auth_headers,
    )

    assert payload["state"] == "completed", payload
    model = db_session.get(Model, payload["model_id"])
    assert model is not None and model.source_url == url
    file_row = db_session.exec(select(File).where(File.model_id == model.id)).first()
    assert file_row is not None and file_row.file_type == FileType.STL
    assert file_row.size_bytes == BENCHY_STL.stat().st_size
