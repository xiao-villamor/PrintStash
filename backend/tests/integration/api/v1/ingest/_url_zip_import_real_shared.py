"""URL + ZIP import coverage driven by *real* benchy testdata and real-world
model-host URLs (Printables / MakerWorld).

These exercise the ``/ingest/url`` and ``/ingest/archive`` surfaces end-to-end:

* A direct-file URL import (as if Printables served the STL directly) ingests
  the real ``testdata/benchy`` mesh and records the source URL on the model.
* A ``.zip`` URL import (as if MakerWorld served a project bundle) resolves to
  an archive manifest, then selective import creates one model per 3D file and
  propagates the source URL to each.
* A plain ``.zip`` upload built from the real testdata files imports the
  importable entries (including binary ``.bgcode``) and skips ``.txt``.

The network is never touched: ``download_to_staging`` is mocked to stage a
*copy* of the real testdata file (ingestion *moves* staged blobs into the
vault, so the originals under ``testdata/`` are never disturbed), the SSRF
guard is mocked so the real public hosts don't require DNS in CI, and — for the
model-*page* URLs — ``resolve_page_url`` is mocked so the host download APIs
(Printables GraphQL, MakerWorld) aren't called. The provided URLs are still
threaded through as the real ``source_url`` values. Resolver internals are
covered separately in ``test_import_resolvers.py``.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.config import _overlay, settings
from app.db.models import Collection, File, FileType, Model
from app.services import import_resolvers, importer
from tests.paths import REPO_ROOT

# --------------------------------------------------------------------------- #
# Real fixtures + real-world URLs
# --------------------------------------------------------------------------- #
TESTDATA = REPO_ROOT / "testdata"

BENCHY_STL = TESTDATA / "benchy" / "3dbenchy.stl"
BENCHY_GCODE_A = TESTDATA / "benchy" / "3dbenchy_PLA_1h12m.gcode"
BENCHY_GCODE_B = TESTDATA / "benchy" / "3dbenchy_PLA_1h13m.gcode"
BENCHY_BGCODE = TESTDATA / "benchy" / "BenchyRules_PLA_14m.bgcode"

# The exact model-page URLs a user would paste from each host.
PRINTABLES_URL = "https://www.printables.com/model/3161-3d-benchy"
MAKERWORLD_URL = (
    "https://makerworld.com/es/models/1123776-original-3d-benchy"
    "?from=search#profileId-1355120"
)


def _requires(*paths: Path):
    missing = [p for p in paths if not p.exists()]
    return pytest.mark.skipif(
        bool(missing), reason=f"missing real fixture(s): {missing}"
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _configure_storage(tmp_path: Path) -> None:
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"
    settings.incoming_dir.mkdir(parents=True, exist_ok=True)


def _stage_bytes(data: bytes, suffix: str) -> Path:
    """Drop bytes into the staging incoming dir as a real download would."""
    staged = settings.incoming_dir / f"{uuid.uuid4().hex}{suffix}"
    staged.write_bytes(data)
    return staged


def _fake_download(staged: Path, original_filename: str) -> AsyncMock:
    """Mock for ``download_to_staging`` that yields an already-staged file."""

    async def _dl(url: str):  # signature mirrors the real coroutine
        return staged, original_filename

    return AsyncMock(side_effect=_dl)


@contextmanager
def _patch_resolver(resolved_url: str | None):
    """Patch page resolution so model-page URLs never hit the real host APIs.

    ``resolved_url`` is the direct download URL the resolver would return for a
    recognised page; ``None`` mimics an unrecognised host (direct download).
    Also stubs ``list_model_files`` to ``None`` so the single-file path is taken
    (the multi-file manifest branch is covered separately).
    """
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "app.api.v1.ingest.import_resolvers.resolve_page_url",
                new=AsyncMock(return_value=resolved_url),
            )
        )
        stack.enter_context(
            patch(
                "app.api.v1.ingest.import_resolvers.list_model_files",
                new=AsyncMock(return_value=None),
            )
        )
        yield


def _benchy_zip_bytes(*sources: Path) -> bytes:
    """Build a real .zip from testdata files, preserving their names."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for src in sources:
            zf.writestr(f"3DBenchy/{src.name}", src.read_bytes())
        zf.writestr("3DBenchy/README.txt", b"original 3DBenchy by CreativeTools")
    return buf.getvalue()


def _job(client: TestClient, resp, headers: dict[str, str]) -> dict:
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    job = client.get(f"/api/v1/ingest/jobs/{job_id}", headers=headers)
    assert job.status_code == 200, job.text
    return job.json()


# --------------------------------------------------------------------------- #
# download_to_staging — exercised against a real server (no mocking)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Full /ingest/url path — real download + real ingest, only SSRF relaxed
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# URL import — direct file (Printables)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# URL import — .zip bundle (MakerWorld)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# ZIP upload — built from real testdata files
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# URL import — pasting a model *page* (HTML), not a direct file
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Collection import + multi-file model-page selection
# --------------------------------------------------------------------------- #
PRINTABLES_COLLECTION_URL = (
    "https://www.printables.com/@JonasHansen_1131321/collections/3525050"
)


def _fake_download_seq(items: list[tuple[bytes, str]]) -> AsyncMock:
    """``download_to_staging`` mock that stages a *fresh* copy per call.

    Successive calls return successive ``items`` so distinct members/files
    produce distinct content hashes (a shared copy would dedup into one model).
    """
    pending = list(items)

    async def _dl(url: str):
        data, filename = pending.pop(0)
        staged = _stage_bytes(data, Path(filename).suffix or ".bin")
        return staged, filename

    return AsyncMock(side_effect=_dl)


__all__ = [
    "AsyncMock",
    "BENCHY_BGCODE",
    "BENCHY_GCODE_A",
    "BENCHY_GCODE_B",
    "BENCHY_STL",
    "Collection",
    "File",
    "FileType",
    "MAKERWORLD_URL",
    "Model",
    "PRINTABLES_COLLECTION_URL",
    "PRINTABLES_URL",
    "Path",
    "Session",
    "TestClient",
    "_benchy_zip_bytes",
    "_configure_storage",
    "_fake_download",
    "_fake_download_seq",
    "_job",
    "_patch_resolver",
    "_requires",
    "_stage_bytes",
    "import_resolvers",
    "importer",
    "io",
    "patch",
    "select",
    "zipfile",
]
