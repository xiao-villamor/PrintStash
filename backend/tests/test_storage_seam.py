"""Tests for the deepened StorageBackend interface (direct_path / local_path /
move_in) and the live/trashed query scopes."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.db.models import Model
from app.db.scopes import live, trashed
from app.services.storage_backend import (
    LocalStorageBackend,
    S3StorageBackend,
    StorageBackend,
)
from app.services.trash import restore_model, soft_delete_model


class _FakeRemoteBackend(LocalStorageBackend):
    """Backend with no direct filesystem representation — exercises the
    temp-download path of ``local_path()`` and the upload path of
    ``move_in()`` without S3."""

    def __init__(self, store_dir: Path) -> None:
        self._store = store_dir

    def direct_path(self, key: str) -> Path | None:
        return None

    def _resolve(self, key: str) -> Path:
        return self._store / key

    def download_to_path(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self._resolve(key).read_bytes())
        return dest

    def upload_file(self, src: Path, key: str) -> None:
        dest = self._resolve(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())


def test_local_backend_local_path_yields_real_path(tmp_path: Path) -> None:
    backend: StorageBackend = LocalStorageBackend()
    blob = tmp_path / "part.stl"
    blob.write_bytes(b"solid")

    with backend.local_path(str(blob)) as path:
        assert path == blob
        assert path.read_bytes() == b"solid"
    # Real path must survive the context exit.
    assert blob.exists()


def test_remote_backend_local_path_downloads_and_cleans_up(tmp_path: Path) -> None:
    backend = _FakeRemoteBackend(tmp_path / "store")
    (tmp_path / "store").mkdir()
    (tmp_path / "store" / "key.gcode").write_bytes(b"G1 X0")

    seen: Path | None = None
    with backend.local_path("key.gcode") as path:
        seen = path
        assert path.read_bytes() == b"G1 X0"
        assert path != tmp_path / "store" / "key.gcode"  # temp copy
    assert seen is not None and not seen.exists()  # cleaned up on exit


def test_local_backend_move_in_renames(tmp_path: Path) -> None:
    backend = LocalStorageBackend()
    staged = tmp_path / "staged.bin"
    staged.write_bytes(b"data")
    dest = tmp_path / "vault" / "v1" / "staged.bin"

    backend.move_in(staged, str(dest))

    assert not staged.exists()
    assert dest.read_bytes() == b"data"


def test_remote_backend_move_in_uploads_and_removes_staged(tmp_path: Path) -> None:
    backend = _FakeRemoteBackend(tmp_path / "store")
    staged = tmp_path / "staged.bin"
    staged.write_bytes(b"data")

    backend.move_in(staged, "blobs/staged.bin")

    assert not staged.exists()
    assert (tmp_path / "store" / "blobs" / "staged.bin").read_bytes() == b"data"


def test_live_and_trashed_scopes(db_session: Session) -> None:
    m = Model(name="ScopeTest", slug="scope-test", hash="f" * 64)
    db_session.add(m)
    db_session.commit()
    db_session.refresh(m)

    assert m in db_session.exec(select(Model).where(live(Model))).all()
    assert m not in db_session.exec(select(Model).where(trashed(Model))).all()

    soft_delete_model(db_session, m)
    assert m not in db_session.exec(select(Model).where(live(Model))).all()
    assert m in db_session.exec(select(Model).where(trashed(Model))).all()

    restore_model(db_session, m)
    assert m in db_session.exec(select(Model).where(live(Model))).all()


# ---------------------------------------------------------------------------
# S3 exists(): only a genuine miss is False
# ---------------------------------------------------------------------------


def _s3_backend_raising(error_code: str) -> S3StorageBackend:
    """An S3 backend whose head_object always fails with *error_code*.

    Built without __init__ so no boto3 client or bucket config is needed.
    """
    import botocore.exceptions

    class _Client:
        def head_object(self, **_kwargs):
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": error_code, "Message": error_code}}, "HeadObject"
            )

    backend = object.__new__(S3StorageBackend)
    backend._client = _Client()  # type: ignore[attr-defined]
    backend._bucket = "test-bucket"  # type: ignore[attr-defined]
    return backend


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
def test_s3_exists_false_on_missing_key(code: str) -> None:
    assert _s3_backend_raising(code).exists("some/key") is False


@pytest.mark.parametrize("code", ["403", "AccessDenied", "InvalidAccessKeyId"])
def test_s3_exists_raises_on_auth_error(code: str) -> None:
    """A credential failure must never read as 'the blob is gone' — that is how
    the orphan-blob GC talks itself into deleting the whole bucket."""
    import botocore.exceptions

    with pytest.raises(botocore.exceptions.ClientError):
        _s3_backend_raising(code).exists("some/key")


def test_s3_object_info_exposes_remote_etag_and_size() -> None:
    class _Client:
        def head_object(self, **_kwargs):
            return {"ContentLength": 42, "ETag": '"remote-etag"'}

    backend = object.__new__(S3StorageBackend)
    backend._client = _Client()  # type: ignore[attr-defined]
    backend._bucket = "test-bucket"  # type: ignore[attr-defined]

    info = backend.object_info("thumb.webp")

    assert info is not None
    assert info.size == 42
    assert info.etag == '"remote-etag"'


# ---------------------------------------------------------------------------
# S3 _ensure_bucket(): create-if-missing on startup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", ["404", "NoSuchBucket", "NotFound"])
def test_s3_ensure_bucket_creates_on_missing(
    code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A HeadBucket miss (real S3 raises "NoSuchBucket"; MinIO's bodyless HEAD
    response makes boto3 fall back to the raw HTTP status "404") must trigger
    bucket creation, not propagate — found by running against real MinIO,
    where the previous check (`Error.StatusCode`, a key that doesn't exist on
    a ClientError) never matched and startup always failed."""
    import botocore.exceptions

    from app.core.config import _overlay

    class _Client:
        created: list[dict] = []

        def head_bucket(self, **_kwargs):
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": code, "Message": code}}, "HeadBucket"
            )

        def create_bucket(self, **kwargs):
            self.created.append(kwargs)

    backend = object.__new__(S3StorageBackend)
    backend._client = _Client()  # type: ignore[attr-defined]
    backend._bucket = "test-bucket"  # type: ignore[attr-defined]
    monkeypatch.setitem(_overlay, "s3_region", "auto")

    backend._ensure_bucket()  # type: ignore[attr-defined]

    assert backend._client.created == [  # type: ignore[attr-defined]
        {"Bucket": "test-bucket", "CreateBucketConfiguration": {}}
    ]


def test_s3_ensure_bucket_raises_on_auth_error() -> None:
    import botocore.exceptions

    class _Client:
        def head_bucket(self, **_kwargs):
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "403", "Message": "403"}}, "HeadBucket"
            )

        def create_bucket(self, **_kwargs):
            raise AssertionError("must not attempt to create on a non-404 error")

    backend = object.__new__(S3StorageBackend)
    backend._client = _Client()  # type: ignore[attr-defined]
    backend._bucket = "test-bucket"  # type: ignore[attr-defined]

    with pytest.raises(botocore.exceptions.ClientError):
        backend._ensure_bucket()  # type: ignore[attr-defined]
