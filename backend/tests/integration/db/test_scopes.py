"""Tests for the deepened StorageBackend interface (direct_path / local_path /
move_in) and the live/trashed query scopes."""

from __future__ import annotations

import hashlib
from io import BytesIO
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import Model
from app.db.scopes import live, trashed
from app.services import storage_backend
from app.services.storage_backend import (
    CreationReceipt,
    LocalStorageBackend,
    S3StorageBackend,
    StorageBackend,
    StorageCollisionError,
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

    def create_stream(self, src, key: str):
        return LocalStorageBackend().create_stream(src, str(self._resolve(key)))


def test_get_backend_requires_explicit_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage_backend, "_backend", None)

    with pytest.raises(RuntimeError, match="storage_backend_not_bound"):
        storage_backend.get_backend()

    bound = LocalStorageBackend()
    assert storage_backend.bind_backend(bound) is bound
    assert storage_backend.get_backend() is bound


def test_create_backend_selects_adapter_without_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Local:
        pass

    class _S3:
        pass

    monkeypatch.setattr(storage_backend, "LocalStorageBackend", _Local)
    monkeypatch.setattr(storage_backend, "S3StorageBackend", _S3)

    assert isinstance(storage_backend.create_backend("local"), _Local)
    assert isinstance(storage_backend.create_backend("s3"), _S3)
    assert isinstance(storage_backend.create_backend("unexpected"), _Local)


def test_init_backend_validates_before_binding(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class _Backend:
        def ensure_setup(self) -> None:
            events.append("validated")

    backend = _Backend()
    monkeypatch.setattr(storage_backend, "_backend", None)
    monkeypatch.setattr(storage_backend, "create_backend", lambda _name: backend)

    assert storage_backend.init_backend() is backend
    assert events == ["validated"]
    assert storage_backend.get_backend() is backend


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


def test_local_backend_move_in_never_overwrites_existing_file(tmp_path: Path) -> None:
    backend = LocalStorageBackend()
    staged = tmp_path / "staged.bin"
    staged.write_bytes(b"new data")
    dest = tmp_path / "vault" / "v1" / "staged.bin"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"existing data")

    with pytest.raises(FileExistsError):
        backend.move_in(staged, str(dest))

    assert staged.read_bytes() == b"new data"
    assert dest.read_bytes() == b"existing data"


def test_remote_backend_move_in_uploads_and_removes_staged(tmp_path: Path) -> None:
    backend = _FakeRemoteBackend(tmp_path / "store")
    staged = tmp_path / "staged.bin"
    staged.write_bytes(b"data")

    backend.move_in(staged, "blobs/staged.bin")

    assert not staged.exists()
    assert (tmp_path / "store" / "blobs" / "staged.bin").read_bytes() == b"data"


def test_move_in_returns_creation_proof_when_staged_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(_overlay, "data_dir", tmp_path / "vault")
    monkeypatch.setitem(_overlay, "thumb_dir", tmp_path / "thumbs")
    monkeypatch.setitem(_overlay, "backup_dir", tmp_path / "backups")
    backend = LocalStorageBackend()
    staged = tmp_path / "staged.bin"
    staged.write_bytes(b"data")
    destination = tmp_path / "vault" / "staged.bin"
    original_unlink = Path.unlink

    def fail_only_source(self: Path, *args, **kwargs):
        if self == staged:
            raise PermissionError("staged source became read-only")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_only_source)

    receipt = backend.move_in(staged, str(destination))

    assert receipt.key == str(destination)
    assert backend.creation_matches(receipt)
    assert staged.read_bytes() == b"data"
    assert destination.read_bytes() == b"data"


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
    """A credential failure must never be reported as 'the blob is gone'."""
    import botocore.exceptions

    with pytest.raises(botocore.exceptions.ClientError):
        _s3_backend_raising(code).exists("some/key")


@pytest.mark.parametrize("code", ["404", "NoSuchKey", "NotFound"])
def test_s3_stat_size_maps_missing_race_to_file_not_found(code: str) -> None:
    backend = _s3_backend_raising(code)

    with pytest.raises(FileNotFoundError):
        backend.stat_size("some/key")


def test_s3_stat_size_preserves_non_missing_client_error() -> None:
    import botocore.exceptions

    with pytest.raises(botocore.exceptions.ClientError):
        _s3_backend_raising("AccessDenied").stat_size("some/key")


def test_s3_get_object_missing_after_exists_maps_to_file_not_found() -> None:
    import botocore.exceptions

    class _Client:
        def head_object(self, **_kwargs):
            return {"ContentLength": 12}

        def get_object(self, **_kwargs):
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "deleted"}},
                "GetObject",
            )

    backend = object.__new__(S3StorageBackend)
    backend._client = _Client()  # type: ignore[attr-defined]
    backend._bucket = "test-bucket"  # type: ignore[attr-defined]

    # This models the endpoint's exists() check succeeding immediately before
    # the object is deleted by another actor.
    assert backend.exists("some/key") is True
    assert backend.stat_size("some/key") == 12
    with pytest.raises(FileNotFoundError):
        list(backend.stream_chunks("some/key"))


def test_s3_get_object_preserves_non_missing_client_error() -> None:
    import botocore.exceptions

    class _Client:
        def get_object(self, **_kwargs):
            raise botocore.exceptions.ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "forbidden"}},
                "GetObject",
            )

    backend = object.__new__(S3StorageBackend)
    backend._client = _Client()  # type: ignore[attr-defined]
    backend._bucket = "test-bucket"  # type: ignore[attr-defined]

    with pytest.raises(botocore.exceptions.ClientError):
        list(backend.stream_chunks("some/key"))


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


def test_s3_lifecycle_audit_reports_expiration_covering_managed_prefix() -> None:
    class _Client:
        def get_bucket_lifecycle_configuration(self, **_kwargs):
            return {
                "Rules": [
                    {
                        "ID": "expire-vault",
                        "Status": "Enabled",
                        "Filter": {"Prefix": "vault-data/"},
                        "Expiration": {"Days": 30},
                    },
                    {
                        "ID": "safe-backups",
                        "Status": "Enabled",
                        "Filter": {"Prefix": "backups/"},
                        "Expiration": {"Days": 30},
                    },
                ]
            }

    backend = object.__new__(S3StorageBackend)
    backend._client = _Client()  # type: ignore[attr-defined]
    backend._bucket = "test-bucket"  # type: ignore[attr-defined]

    assert backend.destructive_lifecycle_findings() == [
        {
            "rule_id": "expire-vault",
            "prefix": "vault-data/",
            "expiration": {"Days": 30},
        }
    ]


def test_s3_create_is_conditional_and_rollback_requires_operation_token() -> None:
    import botocore.exceptions

    class _Client:
        def __init__(self) -> None:
            self.objects: dict[str, tuple[bytes, dict[str, str], str]] = {}

        def put_object(self, **kwargs):
            key = kwargs["Key"]
            assert kwargs["IfNoneMatch"] == "*"
            if key in self.objects:
                raise botocore.exceptions.ClientError(
                    {
                        "Error": {"Code": "PreconditionFailed"},
                        "ResponseMetadata": {"HTTPStatusCode": 412},
                    },
                    "PutObject",
                )
            body = kwargs["Body"]
            data = body.read() if hasattr(body, "read") else bytes(body)
            etag = '"etag-1"'
            self.objects[key] = (data, kwargs["Metadata"], etag)
            return {"ETag": etag}

        def head_object(self, **kwargs):
            data, metadata, etag = self.objects[kwargs["Key"]]
            return {"ContentLength": len(data), "Metadata": metadata, "ETag": etag}

        def delete_object(self, **kwargs):
            assert kwargs["IfMatch"] == self.objects[kwargs["Key"]][2]
            del self.objects[kwargs["Key"]]

    backend = object.__new__(S3StorageBackend)
    backend._client = _Client()  # type: ignore[attr-defined]
    backend._bucket = "vault"  # type: ignore[attr-defined]

    receipt = backend.create_bytes(b"owned", "vault-data/files/part.stl")
    with pytest.raises(StorageCollisionError):
        backend.create_bytes(b"replacement", receipt.key)

    data, _metadata, etag = backend._client.objects[receipt.key]  # type: ignore[attr-defined]
    backend._client.objects[receipt.key] = (  # type: ignore[attr-defined]
        data,
        {"printstash-create-token": "another-operation"},
        etag,
    )
    assert backend.rollback_create(receipt) is False
    assert receipt.key in backend._client.objects  # type: ignore[attr-defined]


def test_s3_adopt_existing_reconciles_exact_object_and_preserves_collision() -> None:
    class _Client:
        def __init__(self) -> None:
            self.objects: dict[str, tuple[bytes, dict[str, str], str]] = {}

        def head_object(self, **kwargs):
            data, metadata, etag = self.objects[kwargs["Key"]]
            return {
                "ContentLength": len(data),
                "Metadata": metadata,
                "ETag": etag,
                "VersionId": "version-1",
            }

        def get_object(self, **kwargs):
            data, _metadata, _etag = self.objects[kwargs["Key"]]
            return {"Body": BytesIO(data)}

    backend = object.__new__(S3StorageBackend)
    backend._client = _Client()  # type: ignore[attr-defined]
    backend._bucket = "vault"  # type: ignore[attr-defined]
    key = "vault-data/capture-slots/pending"
    payload = b"published-before-receipt"
    backend._client.objects[key] = (  # type: ignore[attr-defined]
        payload,
        {"printstash-create-token": "operation-token"},
        '"etag-1"',
    )

    receipt = backend.adopt_existing(
        key,
        expected_size=len(payload),
        expected_sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert receipt.token == "operation-token"
    assert receipt.version_id == "version-1"
    assert backend.creation_matches(receipt)

    backend._client.objects[key] = (  # type: ignore[attr-defined]
        b"different-owner-bytes",
        {"printstash-create-token": "operation-token"},
        '"etag-2"',
    )
    with pytest.raises(StorageCollisionError):
        backend.adopt_existing(
            key,
            expected_size=len(payload),
            expected_sha256=hashlib.sha256(payload).hexdigest(),
        )
    assert backend._client.objects[key][0] == b"different-owner-bytes"  # type: ignore[attr-defined]

    # Copying PrintStash metadata onto different bytes is still not proof that
    # the current object is the exact create operation in the receipt.
    backend._client.objects[receipt.key] = (  # type: ignore[attr-defined]
        b"changed",
        {"printstash-create-token": receipt.token},
        '"etag-2"',
    )
    assert backend.rollback_create(receipt) is False
    assert receipt.key in backend._client.objects  # type: ignore[attr-defined]


def test_s3_rollback_deletes_exact_version_and_preserves_same_etag_replacement() -> (
    None
):
    class _Client:
        versions = {
            "old": {
                "ContentLength": 5,
                "Metadata": {"printstash-create-token": "owned-token"},
                "ETag": '"same-etag"',
            },
            "new": {
                "ContentLength": 5,
                "Metadata": {"printstash-create-token": "new-token"},
                "ETag": '"same-etag"',
            },
        }

        def head_object(self, **kwargs):
            return self.versions[kwargs.get("VersionId", "new")]

        def delete_object(self, **kwargs):
            del self.versions[kwargs["VersionId"]]

    backend = object.__new__(S3StorageBackend)
    backend._client = _Client()  # type: ignore[attr-defined]
    backend._bucket = "vault"  # type: ignore[attr-defined]
    receipt = CreationReceipt(
        key="vault-data/files/part.stl",
        size=5,
        token="owned-token",
        backend="s3",
        namespace="vault/vault-data/",
        etag='"same-etag"',
        version_id="old",
    )

    assert backend.rollback_create(receipt) is True
    assert "old" not in backend._client.versions  # type: ignore[attr-defined]
    assert "new" in backend._client.versions  # type: ignore[attr-defined]


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
