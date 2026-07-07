"""Smoke tests for the ingest surface the README advertises:

- Import from a ``.zip`` archive (stage → select → import).
- Import from a direct URL (SSRF guard + single-file import).
- Per-format source-mesh uploads (STL is covered in test_ingest_api; here we
  add OBJ, 3MF, and STEP/STP routing).

Fixtures are generated at runtime with trimesh so no binary blobs are committed.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import trimesh
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import Collection, File, FileType, Model


def _configure_storage(tmp_path: Path) -> None:
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"


def _mesh_bytes(ext: str, size: tuple[float, float, float] = (10, 10, 10)) -> bytes:
    """A valid box mesh serialized to the requested format."""
    out = trimesh.creation.box(size).export(file_type=ext)
    return out if isinstance(out, bytes) else out.encode()


def _completed(client: TestClient, resp, headers: dict[str, str]) -> dict:
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    job = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=headers)
    assert job.status_code == 200, job.text
    return job.json()


# --------------------------------------------------------------------------- #
# Per-format source mesh uploads
# --------------------------------------------------------------------------- #


def test_ingest_obj_creates_model(
    tmp_path: Path, client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    payload = _completed(
        client,
        client.post(
            "/api/v1/ingest/model",
            headers=auth_headers,
            files={"file": ("widget.obj", _mesh_bytes("obj"), "text/plain")},
            data={"model_name": "OBJ Widget"},
        ),
        auth_headers,
    )
    assert payload["state"] == "completed", payload
    file_row = db_session.get(File, payload["file_id"])
    assert file_row is not None and file_row.file_type == FileType.OBJ


def test_ingest_3mf_creates_model(
    tmp_path: Path, client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    payload = _completed(
        client,
        client.post(
            "/api/v1/ingest/model",
            headers=auth_headers,
            files={"file": ("widget.3mf", _mesh_bytes("3mf"), "model/3mf")},
            data={"model_name": "3MF Widget"},
        ),
        auth_headers,
    )
    assert payload["state"] == "completed", payload
    file_row = db_session.get(File, payload["file_id"])
    assert file_row is not None and file_row.file_type == FileType.THREE_MF


def test_ingest_step_is_accepted_and_typed(
    tmp_path: Path, client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    """A .step upload is accepted, routed to the CAD pipeline, and stored as STEP.

    We don't ship a real STEP fixture (hand-authoring valid B-rep is brittle), so
    the bogus body fails tessellation — but ingestion degrades gracefully and
    still persists the source file typed as STEP. End-to-end STEP tessellation
    (mesh + thumbnail) is exercised once a safe fixture is contributed.
    """
    _configure_storage(tmp_path)
    resp = client.post(
        "/api/v1/ingest/model",
        headers=auth_headers,
        files={"file": ("part.step", b"ISO-10303-21;\nnot-a-real-step\n", "application/step")},
        data={"model_name": "STEP Part"},
    )
    assert resp.status_code == 202, resp.text
    payload = _completed(client, resp, auth_headers)
    assert payload["state"] == "completed", payload
    file_row = db_session.get(File, payload["file_id"])
    assert file_row is not None and file_row.file_type == FileType.STEP


def test_ingest_rejects_unsupported_type(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    resp = client.post(
        "/api/v1/ingest/model",
        headers=auth_headers,
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"model_name": "Nope"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unsupported_file_type"


# --------------------------------------------------------------------------- #
# ZIP archive import
# --------------------------------------------------------------------------- #


def test_import_zip_archive_creates_models(
    tmp_path: Path, client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("parts/part_a.stl", _mesh_bytes("stl", (10, 10, 10)))
        zf.writestr("parts/part_b.stl", _mesh_bytes("stl", (20, 10, 10)))
        zf.writestr("readme.txt", b"not a 3d file")

    manifest = client.post(
        "/api/v1/ingest/archive",
        headers=auth_headers,
        files={"file": ("bundle.zip", buf.getvalue(), "application/zip")},
    )
    assert manifest.status_code == 200, manifest.text
    body = manifest.json()
    archive_id = body["archive_id"]
    importable = [e["name"] for e in body["entries"] if e["file_type"]]
    assert sorted(importable) == ["parts/part_a.stl", "parts/part_b.stl"]

    payload = _completed(
        client,
        client.post(
            f"/api/v1/ingest/archive/{archive_id}/select",
            headers=auth_headers,
            json={"names": importable},
        ),
        auth_headers,
    )
    assert payload["state"] == "completed", payload
    assert payload["result"]["imported"] == 2

    # Both files sit under the zip's "parts/" folder, so they are mirrored into
    # a sub-collection ("bundle/parts") nested beneath the archive's auto
    # collection rather than flattened onto it.
    collection = db_session.exec(
        select(Collection).where(Collection.path == "bundle/parts")
    ).first()
    assert collection is not None
    models = db_session.exec(
        select(Model).where(Model.collection_id == collection.id)
    ).all()
    assert len(models) == 2


def test_import_archive_select_unknown_id_404(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)
    resp = client.post(
        "/api/v1/ingest/archive/does-not-exist/select",
        headers=auth_headers,
        json={"names": ["x.stl"]},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "archive_not_found"


# --------------------------------------------------------------------------- #
# URL import
# --------------------------------------------------------------------------- #


def test_import_from_url_single_file(
    tmp_path: Path, client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    _configure_storage(tmp_path)

    async def _fake_download(url: str):
        staging = Path(_overlay["staging_dir"])
        staging.mkdir(parents=True, exist_ok=True)
        staged = staging / "remote-cube.stl"
        staged.write_bytes(_mesh_bytes("stl"))
        return staged, "remote-cube.stl"

    with (
        patch("app.api.v1.ingest.importer.validate_public_url", return_value=None),
        patch(
            "app.api.v1.ingest.importer.download_to_staging",
            new=AsyncMock(side_effect=_fake_download),
        ),
    ):
        payload = _completed(
            client,
            client.post(
                "/api/v1/ingest/url",
                headers=auth_headers,
                json={"url": "https://example.com/remote-cube.stl"},
            ),
            auth_headers,
        )

    # The archive/URL import pipeline reports model_id on the parent job; the
    # per-file file_id lives in result["items"].
    assert payload["state"] == "completed", payload
    assert payload["model_id"] is not None
    file_row = db_session.exec(
        select(File).where(File.model_id == payload["model_id"])
    ).first()
    assert file_row is not None and file_row.file_type == FileType.STL


def test_import_from_url_blocks_private_host(
    tmp_path: Path, client: TestClient, auth_headers: dict[str, str]
) -> None:
    """SSRF guard: a loopback/private target is rejected before any fetch."""
    _configure_storage(tmp_path)
    resp = client.post(
        "/api/v1/ingest/url",
        headers=auth_headers,
        json={"url": "http://127.0.0.1:7125/secret.stl"},
    )
    assert resp.status_code == 400, resp.text
