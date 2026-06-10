"""Storage backend abstraction: local filesystem and S3-compatible (R2, MinIO)."""

from __future__ import annotations

from contextlib import contextmanager
import os
import shutil
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import BinaryIO, Iterator

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class StorageBackend(ABC):
    """Abstract interface for vault file operations.

    Keys are opaque identifiers: for the local backend they are absolute
    filesystem paths; for S3 they are object keys within the bucket.

    Callers must never branch on the concrete backend type. Anything that
    needs a real filesystem path uses ``local_path()``; anything moving a
    staged upload into the vault uses ``move_in()``; HTTP handlers deciding
    between file and streaming responses use ``direct_path()``.
    """

    @abstractmethod
    def blob_key(self, slug: str, version: int, filename: str) -> str: ...

    @abstractmethod
    def thumbnail_key(self, file_id: int) -> str: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def write_stream(self, src: BinaryIO, key: str) -> int: ...

    @abstractmethod
    def write_bytes(self, data: bytes, key: str) -> int: ...

    @abstractmethod
    def move(self, src_key: str, dest_key: str) -> None: ...

    @abstractmethod
    def stat_size(self, key: str) -> int: ...

    @abstractmethod
    def read_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def stream_chunks(
        self, key: str, chunk_size: int = 1024 * 1024
    ) -> Iterator[bytes]: ...

    @abstractmethod
    def download_to_path(self, key: str, dest: Path) -> Path: ...

    @abstractmethod
    def upload_file(self, src: Path, key: str) -> None: ...

    @abstractmethod
    def ensure_setup(self) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def list_keys(self, prefix: str = "") -> list[str]: ...

    @abstractmethod
    def walk_keys(self, prefix: str = "") -> Iterator[str]: ...

    @abstractmethod
    def usage(self, prefix: str = "") -> dict: ...

    @abstractmethod
    def presigned_download_url(self, key: str, filename: str) -> str | None: ...

    @abstractmethod
    def health_probe(self) -> dict: ...

    @abstractmethod
    def direct_path(self, key: str) -> Path | None:
        """Return the on-disk path for *key*, or None when the backend has
        no direct filesystem representation (S3)."""
        ...

    @contextmanager
    def local_path(self, key: str) -> Iterator[Path]:
        """Yield a local filesystem path for *key*.

        Local backend yields the real path. Remote backends download to a
        temporary file and remove it on exit. The single owner of the
        temp-file lifecycle — callers never manage cleanup.
        """
        direct = self.direct_path(key)
        if direct is not None:
            yield direct
            return
        fd, name = tempfile.mkstemp(suffix=Path(key).suffix)
        os.close(fd)
        tmp = Path(name)
        try:
            self.download_to_path(key, tmp)
            yield tmp
        finally:
            tmp.unlink(missing_ok=True)

    def move_in(self, src: Path, dest_key: str) -> None:
        """Move a local staged file into the vault at *dest_key*.

        Local backend renames; remote backends upload then remove the
        staged file.
        """
        if self.direct_path(dest_key) is not None:
            self.move(str(src), dest_key)
            return
        self.upload_file(src, dest_key)
        try:
            src.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Local filesystem backend
# ---------------------------------------------------------------------------


class LocalStorageBackend(StorageBackend):
    def direct_path(self, key: str) -> Path | None:
        return Path(key)

    def blob_key(self, slug: str, version: int, filename: str) -> str:
        return str(settings.data_dir / slug / f"v{version}" / filename)

    def thumbnail_key(self, file_id: int) -> str:
        return str(settings.thumb_dir / f"{file_id}.png")

    def exists(self, key: str) -> bool:
        return Path(key).exists()

    def write_stream(self, src: BinaryIO, key: str) -> int:
        dest = Path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        with dest.open("wb") as out:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                written += len(chunk)
        return written

    def write_bytes(self, data: bytes, key: str) -> int:
        dest = Path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return len(data)

    def move(self, src_key: str, dest_key: str) -> None:
        dest = Path(dest_key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src_key, str(dest))

    def stat_size(self, key: str) -> int:
        return Path(key).stat().st_size

    def read_bytes(self, key: str) -> bytes:
        return Path(key).read_bytes()

    def stream_chunks(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        with Path(key).open("rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    def download_to_path(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(key, str(dest))
        return dest

    def upload_file(self, src: Path, key: str) -> None:
        dest = Path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dest))

    def ensure_setup(self) -> None:
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.thumb_dir.mkdir(parents=True, exist_ok=True)

    def delete(self, key: str) -> None:
        try:
            Path(key).unlink(missing_ok=True)
        except OSError:
            pass

    def list_keys(self, prefix: str = "") -> list[str]:
        root = Path(prefix) if prefix else settings.data_dir
        if not root.exists():
            return []
        return [str(p) for p in root.rglob("*") if p.is_file()]

    def walk_keys(self, prefix: str = "") -> Iterator[str]:
        root = Path(prefix) if prefix else settings.data_dir
        if not root.exists():
            return
        for p in root.rglob("*"):
            if p.is_file():
                yield str(p)

    def usage(self, prefix: str = "") -> dict:
        root = Path(prefix) if prefix else settings.data_dir
        total_size = 0
        object_count = 0
        if root.exists():
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    total_size += path.stat().st_size
                    object_count += 1
                except OSError:
                    continue
        return {
            "backend": "local",
            "prefix": str(root),
            "object_count": object_count,
            "total_size_bytes": total_size,
        }

    def presigned_download_url(self, key: str, filename: str) -> str | None:
        return None

    def health_probe(self) -> dict:
        data_ok = settings.data_dir.exists()
        thumb_ok = settings.thumb_dir.exists()
        return {
            "backend": "local",
            "ok": data_ok and thumb_ok,
            "data_dir": str(settings.data_dir),
            "thumb_dir": str(settings.thumb_dir),
        }


# ---------------------------------------------------------------------------
# S3-compatible backend (AWS S3, Cloudflare R2, MinIO, etc.)
# ---------------------------------------------------------------------------


class S3StorageBackend(StorageBackend):
    def __init__(self) -> None:
        import boto3
        from botocore.config import Config as BotoConfig

        if not settings.s3_bucket:
            raise RuntimeError("VAULT_S3_BUCKET is required when storage_backend=s3")

        client_kwargs: dict = {
            "service_name": "s3",
            "region_name": settings.s3_region or "auto",
            "aws_access_key_id": settings.s3_access_key or None,
            "aws_secret_access_key": settings.s3_secret_key or None,
            "config": BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
            ),
        }
        if settings.s3_endpoint_url:
            client_kwargs["endpoint_url"] = settings.s3_endpoint_url

        self._client = boto3.client(**client_kwargs)
        self._bucket = settings.s3_bucket

        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        import botocore.exceptions

        try:
            self._client.head_bucket(Bucket=self._bucket)
            logger.info("s3: bucket %r found", self._bucket)
        except botocore.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("StatusCode")
            if code == 404:
                logger.info("s3: creating bucket %r", self._bucket)
                location = (
                    {"LocationConstraint": settings.s3_region}
                    if settings.s3_region and settings.s3_region != "auto"
                    else {}
                )
                self._client.create_bucket(
                    Bucket=self._bucket, CreateBucketConfiguration=location
                )
            else:
                raise

    def _apply_lifecycle_policy(self) -> None:
        expiration_days = int(settings.s3_lifecycle_expiration_days or 0)
        transition_days = int(settings.s3_lifecycle_transition_days or 0)
        if expiration_days <= 0 and transition_days <= 0:
            return
        rule: dict = {
            "ID": "vault-data-lifecycle",
            "Status": "Enabled",
            "Filter": {"Prefix": self._prefix()},
        }
        if transition_days > 0:
            rule["Transitions"] = [
                {
                    "Days": transition_days,
                    "StorageClass": settings.s3_transition_storage_class,
                }
            ]
        if expiration_days > 0:
            rule["Expiration"] = {"Days": expiration_days}
        self._client.put_bucket_lifecycle_configuration(
            Bucket=self._bucket,
            LifecycleConfiguration={"Rules": [rule]},
        )

    def _prefix(self) -> str:
        return "vault-data/"

    def direct_path(self, key: str) -> Path | None:
        return None

    def blob_key(self, slug: str, version: int, filename: str) -> str:
        return f"{self._prefix()}files/{slug}/v{version}/{filename}"

    def thumbnail_key(self, file_id: int) -> str:
        return f"{self._prefix()}thumbs/{file_id}.png"

    def exists(self, key: str) -> bool:
        import botocore.exceptions

        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except botocore.exceptions.ClientError:
            return False

    def write_stream(self, src: BinaryIO, key: str) -> int:
        from boto3.s3.transfer import TransferConfig

        threshold = int(settings.s3_multipart_threshold_mb) * 1024 * 1024
        transfer_cfg = TransferConfig(multipart_threshold=threshold)
        self._client.upload_fileobj(src, self._bucket, key, Config=transfer_cfg)
        return self.stat_size(key)

    def write_bytes(self, data: bytes, key: str) -> int:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)
        return len(data)

    def move(self, src_key: str, dest_key: str) -> None:
        copy_source = {"Bucket": self._bucket, "Key": src_key}
        self._client.copy_object(
            Bucket=self._bucket, Key=dest_key, CopySource=copy_source
        )
        self._client.delete_object(Bucket=self._bucket, Key=src_key)

    def stat_size(self, key: str) -> int:
        resp = self._client.head_object(Bucket=self._bucket, Key=key)
        return resp.get("ContentLength", 0)

    def read_bytes(self, key: str) -> bytes:
        resp = self._client.get_object(Bucket=self._bucket, Key=key)
        return resp["Body"].read()

    def stream_chunks(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        resp = self._client.get_object(Bucket=self._bucket, Key=key)
        body = resp["Body"]
        while True:
            chunk = body.read(chunk_size)
            if not chunk:
                break
            yield chunk

    def download_to_path(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self._bucket, key, str(dest))
        return dest

    def upload_file(self, src: Path, key: str) -> None:
        from boto3.s3.transfer import TransferConfig

        threshold = int(settings.s3_multipart_threshold_mb) * 1024 * 1024
        transfer_cfg = TransferConfig(multipart_threshold=threshold)
        self._client.upload_file(str(src), self._bucket, key, Config=transfer_cfg)

    def ensure_setup(self) -> None:
        self._ensure_bucket()
        self._apply_lifecycle_policy()

    def delete(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except Exception:
            pass

    def list_keys(self, prefix: str = "") -> list[str]:
        full_prefix = prefix or self._prefix()
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def walk_keys(self, prefix: str = "") -> Iterator[str]:
        full_prefix = prefix or self._prefix()
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]

    def usage(self, prefix: str = "") -> dict:
        full_prefix = prefix or self._prefix()
        total_size = 0
        object_count = 0
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                object_count += 1
                total_size += int(obj.get("Size", 0) or 0)
        return {
            "backend": "s3",
            "bucket": self._bucket,
            "prefix": full_prefix,
            "object_count": object_count,
            "total_size_bytes": total_size,
        }

    def presigned_download_url(self, key: str, filename: str) -> str | None:
        return self._client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=int(settings.s3_presigned_url_expire_seconds),
        )

    def health_probe(self) -> dict:
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return {
                "backend": "s3",
                "ok": True,
                "bucket": self._bucket,
                "endpoint": settings.s3_endpoint_url,
            }
        except Exception as exc:
            return {
                "backend": "s3",
                "ok": False,
                "bucket": self._bucket,
                "endpoint": settings.s3_endpoint_url,
                "error": str(exc),
            }


# ---------------------------------------------------------------------------
# Module-level backend singleton
# ---------------------------------------------------------------------------

_backend: StorageBackend | None = None


def get_backend() -> StorageBackend:
    global _backend
    if _backend is None:
        if settings.storage_backend == "s3":
            logger.info(
                "initialising S3 storage backend (bucket=%s)", settings.s3_bucket
            )
            _backend = S3StorageBackend()
        else:
            logger.info("initialising local storage backend")
            _backend = LocalStorageBackend()
    return _backend


def init_backend() -> StorageBackend:
    global _backend
    _backend = get_backend()
    _backend.ensure_setup()
    return _backend
