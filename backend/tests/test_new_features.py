"""Tests for URL/ZIP import, measured filament/duration, auto known-good,
STEP support, and share-link isolation."""

from __future__ import annotations

import asyncio
import io
import zipfile

import pytest

from app.db.models import (
    File,
    FileRevisionStatus,
    Model,
    PrintJob,
    PrintJobState,
    Printer,
    ShareLink,
    SUFFIX_TO_FILE_TYPE,
    FileType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_model(db_session, *, name="M", slug="m", hash_="h" * 64) -> Model:
    m = Model(name=name, slug=slug, hash=hash_)
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)
    return m


def _make_file(db_session, model, *, filename="part.stl", ftype="stl") -> File:
    f = File(
        model_id=model.id,
        path=f"/data/{filename}",
        original_filename=filename,
        file_type=ftype,
        version=1,
        size_bytes=10,
        sha256="a" * 64,
    )
    db_session.add(f)
    db_session.commit()
    db_session.refresh(f)
    return f


# ---------------------------------------------------------------------------
# STEP support
# ---------------------------------------------------------------------------


def test_step_suffixes_map_to_step_filetype():
    assert SUFFIX_TO_FILE_TYPE[".step"] == FileType.STEP
    assert SUFFIX_TO_FILE_TYPE[".stp"] == FileType.STEP


# ---------------------------------------------------------------------------
# Filament conversion
# ---------------------------------------------------------------------------


def test_mm_to_grams_pla_default():
    from app.services import filament

    # 1000 mm of 1.75 mm PLA ≈ 2.40 g/m * ... ~ 2.98 g
    grams = filament.mm_to_grams(1000.0, "PLA")
    assert grams is not None and 2.5 < grams < 3.3


def test_mm_to_grams_handles_bad_input():
    from app.services import filament

    assert filament.mm_to_grams(None) is None
    assert filament.mm_to_grams(0) is None
    assert filament.mm_to_grams(-5) is None


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x.stl",
        "http://127.0.0.1/x.stl",
        "http://localhost/x.stl",
        "http://10.0.0.5/x.stl",
        "http://192.168.1.10/x.stl",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/x.stl",
    ],
)
def test_validate_public_url_rejects_unsafe(url):
    from app.services import importer

    with pytest.raises(importer.ImportError_):
        importer.validate_public_url(url)


def test_validate_public_url_accepts_public_host():
    from app.services import importer

    # Public DNS name should validate (resolves to public IPs).
    importer.validate_public_url("https://example.com/model.stl")


# ---------------------------------------------------------------------------
# Archive inspection: zip-slip + importable filtering
# ---------------------------------------------------------------------------


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_inspect_archive_filters_and_blocks_traversal(tmp_path):
    from app.services import importer

    archive = tmp_path / "pack.zip"
    archive.write_bytes(
        _zip_bytes(
            {
                "good.stl": b"solid",
                "nested/part.3mf": b"x",
                "../evil.stl": b"x",  # traversal — must be dropped
                "readme.txt": b"hi",  # not importable, not image
                "preview.png": b"img",  # image (kept, marked)
            }
        )
    )
    entries = importer.inspect_archive(archive)
    names = {e.name for e in entries}
    assert "good.stl" in names
    assert "nested/part.3mf" in names
    assert "../evil.stl" not in names
    assert "readme.txt" not in names
    image = next(e for e in entries if e.name == "preview.png")
    assert image.is_image and image.file_type is None


def test_extract_selected_only_returns_importable(tmp_path):
    from app.core.config import _overlay
    from app.services import importer

    _overlay["staging_dir"] = tmp_path  # write staged files into the tmp dir
    archive = tmp_path / "pack.zip"
    archive.write_bytes(_zip_bytes({"a.stl": b"solid", "notes.txt": b"x"}))
    out = importer.extract_selected(archive, ["a.stl", "notes.txt"])
    assert len(out) == 1
    staged, name = out[0]
    assert name == "a.stl" and staged.exists()
    staged.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Printer hub: measured filament/duration + auto known-good
# ---------------------------------------------------------------------------


class TestCompletionCapture:
    def _setup(self, db_session, *, revision_status=None):
        m = _make_model(db_session, slug="cap", hash_="c" * 64)
        f = File(
            model_id=m.id,
            path="/data/cap.gcode",
            original_filename="cap.gcode",
            file_type="gcode",
            version=1,
            size_bytes=100,
            sha256="g" * 64,
            revision_status=revision_status,
        )
        db_session.add(f)
        db_session.commit()
        db_session.refresh(f)
        p = Printer(name="Cap", moonraker_url="http://10.0.0.9:7125")
        db_session.add(p)
        db_session.commit()
        db_session.refresh(p)
        job = PrintJob(
            printer_id=p.id,
            file_id=f.id,
            model_id=m.id,
            remote_filename="cap.gcode",
            state=PrintJobState.PRINTING,
            source="vault",
        )
        db_session.add(job)
        db_session.commit()
        db_session.refresh(job)
        return p.id, f.id, job.id

    def test_completion_captures_filament_and_duration(self, hub, db_session):
        pid, file_id, job_id = self._setup(db_session)

        asyncio.run(
            hub._sync_active_job(
                pid,
                "complete",
                "cap.gcode",
                1.0,
                {
                    "state": "complete",
                    "filename": "cap.gcode",
                    "filament_used": 2000.0,
                    "total_duration": 3600,
                },
            )
        )
        job = db_session.get(PrintJob, job_id)
        db_session.refresh(job)
        assert job.state == PrintJobState.COMPLETED
        assert job.filament_used_mm == pytest.approx(2000.0)
        assert job.filament_used_g is not None and job.filament_used_g > 0
        assert job.actual_duration_s == 3600

    def test_completion_auto_marks_known_good(self, hub, db_session):
        pid, file_id, job_id = self._setup(db_session)
        asyncio.run(
            hub._sync_active_job(
                pid, "complete", "cap.gcode", 1.0, {"state": "complete", "filename": "cap.gcode"}
            )
        )
        f = db_session.get(File, file_id)
        db_session.refresh(f)
        assert f.revision_status == FileRevisionStatus.KNOWN_GOOD

    def test_completion_does_not_override_manual_failed(self, hub, db_session):
        pid, file_id, job_id = self._setup(
            db_session, revision_status=FileRevisionStatus.FAILED
        )
        asyncio.run(
            hub._sync_active_job(
                pid, "complete", "cap.gcode", 1.0, {"state": "complete", "filename": "cap.gcode"}
            )
        )
        f = db_session.get(File, file_id)
        db_session.refresh(f)
        assert f.revision_status == FileRevisionStatus.FAILED


# ---------------------------------------------------------------------------
# Share-link isolation
# ---------------------------------------------------------------------------


class TestShareIsolation:
    def _create_share(self, client, auth_headers, model_id, **body):
        payload = {"expires_in_days": 7, "allow_download": False, **body}
        res = client.post(
            f"/api/v1/models/{model_id}/shares", json=payload, headers=auth_headers
        )
        assert res.status_code == 200, res.text
        return res.json()

    def test_public_view_and_token_only_grants_one_model(
        self, client, db_session, auth_headers
    ):
        shared = _make_model(db_session, slug="shared", hash_="s" * 64)
        _make_file(db_session, shared, filename="shared.stl")
        other = _make_model(db_session, slug="other", hash_="o" * 64)
        other_file = _make_file(db_session, other, filename="secret.stl")

        created = self._create_share(client, auth_headers, shared.id)
        token = created["token"]

        # Public detail works without auth.
        res = client.get(f"/api/v1/share/{token}")
        assert res.status_code == 200
        assert res.json()["name"] == "M"

        # A file from a different model is not reachable through this token.
        res = client.get(f"/api/v1/share/{token}/files/{other_file.id}/stl")
        assert res.status_code == 404

    def test_garbage_and_revoked_tokens_404(self, client, db_session, auth_headers):
        m = _make_model(db_session, slug="rev", hash_="r" * 64)
        created = self._create_share(client, auth_headers, m.id)
        token = created["token"]

        assert client.get("/api/v1/share/not-a-real-token").status_code == 404

        # Revoke → 404.
        client.delete(f"/api/v1/shares/{created['id']}", headers=auth_headers)
        assert client.get(f"/api/v1/share/{token}").status_code == 404

    def test_expired_token_404(self, client, db_session, auth_headers):
        from datetime import timedelta
        from app.core.time import utcnow

        m = _make_model(db_session, slug="exp", hash_="e" * 64)
        created = self._create_share(client, auth_headers, m.id)
        link = db_session.get(ShareLink, created["id"])
        link.expires_at = utcnow() - timedelta(days=1)
        db_session.add(link)
        db_session.commit()
        assert client.get(f"/api/v1/share/{created['token']}").status_code == 404

    def test_download_blocked_when_view_only(self, client, db_session, auth_headers):
        m = _make_model(db_session, slug="dl", hash_="d" * 64)
        f = _make_file(db_session, m, filename="dl.stl")
        created = self._create_share(
            client, auth_headers, m.id, allow_download=False
        )
        res = client.get(f"/api/v1/share/{created['token']}/files/{f.id}/download")
        assert res.status_code == 403
