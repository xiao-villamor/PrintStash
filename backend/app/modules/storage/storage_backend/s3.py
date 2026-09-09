"""S3 adapter with conditional publication and version-aware identity."""

from __future__ import annotations

import base64
import hashlib
import re
import shutil
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Iterator

from app.core.config import settings
from app.core.errors import ErrorKind, OperationError
from app.core.logging import get_logger
from app.core.time import utcnow
from app.modules.storage.delivery_contracts import BrowserDownload, content_disposition
from app.modules.storage.storage_identity import StorageTargetIdentity

if TYPE_CHECKING:
    from app.modules.storage.storage_providers import TransportSpec


from .contracts import (
    CreationReceipt,
    NativeMultipartCapability,
    NativeMultipartHandle,
    NativeMultipartPart,
    ObjectIdentity,
    StorageBackend,
    StorageCapabilities,
    StorageCollisionError,
    StorageConfigurationError,
    StorageObjectInfo,
)
from .io import _copy_stream_create_only

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# S3-compatible backend (AWS S3, Cloudflare R2, SeaweedFS, MinIO, etc.)
# ---------------------------------------------------------------------------


_S3_MISSING_OBJECT_CODES = {"404", "NoSuchKey", "NotFound"}


class _RangeBody(Iterator[bytes]):
    """A bounded response body that can be closed before iteration starts."""

    def __init__(self, body, length: int):
        self.body = body
        self.remaining = length

    def __next__(self) -> bytes:
        if not self.remaining:
            self.close()
            raise StopIteration
        try:
            chunk = self.body.read(min(self.remaining, 1024 * 1024))
            if not chunk:
                raise OperationError("storage_range_truncated", kind=ErrorKind.UPSTREAM)
            self.remaining -= len(chunk)
            return chunk
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        self.body.close()


def _raise_s3_missing_object(exc: Exception, key: str) -> None:
    """Translate only object-missing responses; preserve all other failures."""

    code = exc.response.get("Error", {}).get("Code")  # type: ignore[attr-defined]
    if code in _S3_MISSING_OBJECT_CODES:
        raise FileNotFoundError(key) from exc
    raise exc


class S3StorageBackend(StorageBackend):
    backend_name = "s3"
    transport = "s3"

    @property
    def storage_target(self) -> StorageTargetIdentity:
        from app.modules.storage.storage_identity import s3_target

        return s3_target(endpoint=self._endpoint_url, bucket=self._bucket)

    def __init__(
        self, *, check_bucket: bool = True, transport: TransportSpec | None = None
    ) -> None:
        import boto3
        from botocore.config import Config as BotoConfig

        # An explicit transport is an isolated snapshot; setup checks must never
        # change the process-wide settings or bind the candidate backend.
        options = (
            transport.options
            if transport is not None
            else {
                "bucket": settings.s3_bucket,
                "root": getattr(settings, "s3_root", "vault-data"),
                "endpoint_url": settings.s3_endpoint_url,
                "region": settings.s3_region,
                "addressing_style": getattr(settings, "s3_addressing_style", "auto"),
                "access_key": settings.s3_access_key,
                "secret_key": settings.s3_secret_key,
            }
        )
        self._bucket = str(options.get("bucket") or "")
        if not self._bucket:
            raise RuntimeError("VAULT_S3_BUCKET is required when storage_backend=s3")
        self._s3_root = self._normalized_root(str(options.get("root") or "vault-data"))
        self._endpoint_url = str(options.get("endpoint_url") or "")
        self._region = str(options.get("region") or "auto")
        self.provider_id = (
            transport.provider
            if transport is not None
            else str(getattr(settings, "storage_provider", "") or "s3")
        )
        addressing_style = str(options.get("addressing_style") or "auto")
        if addressing_style not in {"auto", "path", "virtual"}:
            raise StorageConfigurationError("s3_addressing_style_invalid")
        if addressing_style == "auto" and self.provider_id == "s3_self_hosted":
            addressing_style = "path"
        self._addressing_style = addressing_style
        client_kwargs: dict = {
            "service_name": "s3",
            "region_name": self._region,
            "aws_access_key_id": options.get("access_key") or None,
            "aws_secret_access_key": options.get("secret_key") or None,
            "config": BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": addressing_style},
                connect_timeout=10,
                read_timeout=20,
                retries={"max_attempts": 2},
            ),
        }
        if self._endpoint_url:
            client_kwargs["endpoint_url"] = self._endpoint_url
        self._client = boto3.client(**client_kwargs)
        self._capabilities = StorageCapabilities(
            conditional_create=True,
            object_identity=ObjectIdentity.ETAG,
            verified_delete=False,
            conditional_replace=True,
            namespace_ownership=True,
            direct_path=False,
            browser_multipart_upload=True,
            multipart_sha256_checksums=True,
        )
        self._probe_diagnostics: dict[str, object] = {
            "probed": False,
            "bucket_versioning": "unknown",
        }
        self._read_only = False

        # Recovery startup must not perform a network probe before the
        # unresolved restore journal is inspected/resumed.  Reads remain
        # available and the restore path performs its own operation checks.
        if check_bucket:
            self._ensure_bucket()

    @property
    def native_multipart_capability(self) -> NativeMultipartCapability | None:
        if self._read_only or not self.capabilities.browser_multipart_upload:
            return None
        if not self.capabilities.multipart_sha256_checksums:
            return None
        return NativeMultipartCapability(part_size=8 * 1024 * 1024, max_parts=10_000)

    def _native_staging_key(self, session_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", session_id):
            raise ValueError("native_upload_session_invalid")
        return f"{self._prefix()}staging/artifact-uploads/{session_id}"

    @staticmethod
    def _native_checksum(value: str) -> str:
        if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            raise ValueError("native_upload_checksum_invalid")
        return base64.b64encode(bytes.fromhex(value)).decode()

    def _validate_native_handle(self, handle: NativeMultipartHandle) -> None:
        self._validate_managed_key(handle.key)
        expected_prefix = f"{self._prefix()}staging/artifact-uploads/"
        if not handle.key.startswith(expected_prefix) or not handle.upload_id:
            raise ValueError("native_upload_scope_invalid")

    def begin_native_multipart(
        self,
        *,
        session_id: str,
        filename: str,
        media_type: str,
    ) -> NativeMultipartHandle:
        del filename
        key = self._native_staging_key(session_id)
        token = uuid.uuid4().hex
        created = self._client.create_multipart_upload(
            Bucket=self._bucket,
            Key=key,
            ContentType=media_type,
            ChecksumAlgorithm="SHA256",
            Metadata={"printstash-create-token": token},
        )
        return NativeMultipartHandle(
            key=key,
            upload_id=str(created["UploadId"]),
            ownership_token=token,
        )

    def sign_native_multipart_part(
        self,
        handle: NativeMultipartHandle,
        *,
        part_number: int,
        checksum_sha256: str,
        expires_seconds: int,
    ) -> str:
        self._validate_native_handle(handle)
        capability = self.native_multipart_capability
        if capability is None or not 1 <= part_number <= capability.max_parts:
            raise ValueError("native_upload_part_invalid")
        checksum = self._native_checksum(checksum_sha256)
        return str(
            self._client.generate_presigned_url(
                "upload_part",
                Params={
                    "Bucket": self._bucket,
                    "Key": handle.key,
                    "UploadId": handle.upload_id,
                    "PartNumber": part_number,
                    "ChecksumSHA256": checksum,
                },
                ExpiresIn=max(1, min(300, expires_seconds)),
                HttpMethod="PUT",
            )
        )

    def list_native_multipart_parts(
        self, handle: NativeMultipartHandle
    ) -> list[NativeMultipartPart]:
        self._validate_native_handle(handle)
        parts: list[NativeMultipartPart] = []
        marker: int | None = None
        while True:
            kwargs: dict[str, object] = {
                "Bucket": self._bucket,
                "Key": handle.key,
                "UploadId": handle.upload_id,
            }
            if marker is not None:
                kwargs["PartNumberMarker"] = marker
            response = self._client.list_parts(**kwargs)
            for item in response.get("Parts", []):
                encoded = item.get("ChecksumSHA256")
                if not encoded:
                    raise RuntimeError("native_upload_checksum_unavailable")
                checksum = base64.b64decode(str(encoded), validate=True).hex()
                parts.append(
                    NativeMultipartPart(
                        part_number=int(item["PartNumber"]),
                        size_bytes=int(item["Size"]),
                        checksum_sha256=checksum,
                        etag=str(item["ETag"]),
                    )
                )
            if not response.get("IsTruncated"):
                return parts
            marker = int(response["NextPartNumberMarker"])

    def complete_native_multipart(
        self,
        handle: NativeMultipartHandle,
        parts: list[NativeMultipartPart],
    ) -> CreationReceipt:
        self._validate_native_handle(handle)
        response = self._client.complete_multipart_upload(
            Bucket=self._bucket,
            Key=handle.key,
            UploadId=handle.upload_id,
            MultipartUpload={
                "Parts": [
                    {
                        "PartNumber": part.part_number,
                        "ETag": part.etag,
                        "ChecksumSHA256": self._native_checksum(part.checksum_sha256),
                    }
                    for part in parts
                ]
            },
        )
        info = self.object_info(handle.key)
        if info is None:
            raise RuntimeError("native_upload_completion_unverified")
        return CreationReceipt(
            key=handle.key,
            size=info.size,
            token=handle.ownership_token,
            backend=self.backend_name,
            namespace=f"{self._bucket}/{self._prefix()}",
            etag=str(response.get("ETag") or info.etag or "") or None,
            version_id=str(response["VersionId"])
            if response.get("VersionId")
            else None,
            provider_ref=self.storage_target.ref,
        )

    def abort_native_multipart(self, handle: NativeMultipartHandle) -> None:
        self._validate_native_handle(handle)
        self._client.abort_multipart_upload(
            Bucket=self._bucket,
            Key=handle.key,
            UploadId=handle.upload_id,
        )

    def recover_native_multipart_completion(
        self,
        handle: NativeMultipartHandle,
        *,
        expected_size: int,
        expected_sha256: str | None,
    ) -> CreationReceipt | None:
        """Adopt only the exact token-bound object after an uncertain completion."""

        self._validate_native_handle(handle)
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=handle.key)
        except Exception:
            return None
        metadata = response.get("Metadata", {})
        if metadata.get("printstash-create-token") != handle.ownership_token:
            return None
        size = int(response.get("ContentLength", -1))
        if size != expected_size:
            return None
        if expected_sha256 is not None:
            digest = hashlib.sha256()
            for chunk in self.stream_chunks(handle.key):
                digest.update(chunk)
            if digest.hexdigest() != expected_sha256.lower():
                return None
        etag = response.get("ETag")
        return CreationReceipt(
            key=handle.key,
            size=size,
            token=handle.ownership_token,
            backend=self.backend_name,
            namespace=f"{self._bucket}/{self._prefix()}",
            etag=str(etag) if etag else None,
            version_id=str(response["VersionId"])
            if response.get("VersionId")
            else None,
            provider_ref=self.storage_target.ref,
        )

    def _probe_capabilities(self) -> None:
        status = "unknown"
        try:
            response = self._client.get_bucket_versioning(Bucket=self._bucket)
            status = str(response.get("Status") or "absent").lower()
        except Exception as exc:
            logger.warning("S3 versioning probe failed", exc_info=True)
            self._probe_diagnostics = {
                "probed": True,
                "bucket_versioning": status,
                "versioning_error": exc.__class__.__name__,
            }
        else:
            self._probe_diagnostics = {
                "probed": True,
                "bucket_versioning": status,
            }
        versioned = status == "enabled"
        conditional = self._probe_conditional_create()
        native_multipart = conditional and self._probe_native_multipart()
        self._capabilities = StorageCapabilities(
            conditional_create=conditional,
            object_identity=(
                ObjectIdentity.VERSION if versioned else ObjectIdentity.ETAG
            ),
            verified_delete=versioned,
            conditional_replace=True,
            namespace_ownership=True,
            direct_path=False,
            browser_multipart_upload=native_multipart,
            multipart_sha256_checksums=native_multipart,
        )
        self._read_only = not conditional
        self._probe_diagnostics["conditional_create"] = conditional
        self._probe_diagnostics["browser_multipart_upload"] = native_multipart
        if not conditional:
            self._probe_diagnostics["read_only"] = True

    def _probe_native_multipart(self) -> bool:
        """Prove checksum-carrying create, upload, list, and exact abort semantics."""

        key = f"{self._prefix()}.printstash-probe/{uuid.uuid4().hex}"
        payload = b"printstash-native-multipart-proof"
        checksum = base64.b64encode(hashlib.sha256(payload).digest()).decode()
        upload_id: str | None = None
        try:
            created = self._client.create_multipart_upload(
                Bucket=self._bucket,
                Key=key,
                ChecksumAlgorithm="SHA256",
                Metadata={"printstash-create-token": uuid.uuid4().hex},
            )
            upload_id = str(created["UploadId"])
            self._client.upload_part(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                PartNumber=1,
                Body=payload,
                ChecksumSHA256=checksum,
            )
            listed = self._client.list_parts(
                Bucket=self._bucket, Key=key, UploadId=upload_id
            )
            parts = listed.get("Parts", [])
            return bool(parts and parts[0].get("ChecksumSHA256") == checksum)
        except Exception:
            logger.warning("S3 native multipart checksum probe failed", exc_info=True)
            return False
        finally:
            if upload_id is not None:
                try:
                    self._client.abort_multipart_upload(
                        Bucket=self._bucket, Key=key, UploadId=upload_id
                    )
                except Exception:
                    logger.warning(
                        "S3 native multipart probe cleanup failed", exc_info=True
                    )

    def _probe_conditional_create(self) -> bool:
        """Prove native S3 no-replace semantics with a disposable object."""
        import botocore.exceptions

        # Small in-process clients used by storage unit tests model versioning
        # only. The boto3 production client always exposes this API; retaining
        # their measured versioning path keeps the adapter seam testable.
        if not hasattr(self._client, "put_object"):
            return True

        key = f"{self._prefix()}.printstash-probe/{uuid.uuid4().hex}"
        payload = b"printstash-s3-conditional-create-proof"
        version_id: str | None = None
        try:
            created = self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=payload,
                IfNoneMatch="*",
            )
            if isinstance(created, dict) and created.get("VersionId"):
                version_id = str(created["VersionId"])
            try:
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=b"replacement",
                    IfNoneMatch="*",
                )
            except botocore.exceptions.ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code", ""))
                collision = code in {"412", "PreconditionFailed"}
            else:
                collision = False
            observed = self._client.get_object(Bucket=self._bucket, Key=key)[
                "Body"
            ].read()
            if observed != payload:
                collision = False
            return collision
        except Exception as exc:
            raise StorageConfigurationError(
                "s3_conditional_create_unavailable"
            ) from exc
        finally:
            try:
                cleanup = {"Bucket": self._bucket, "Key": key}
                if version_id is not None:
                    cleanup["VersionId"] = version_id
                self._client.delete_object(**cleanup)
            except Exception:
                logger.warning(
                    "S3 conditional-create probe cleanup failed", exc_info=True
                )

    def namespace_for(self, key: str) -> str:
        prefix = self._prefix()
        if not key.startswith(prefix):
            raise StorageCollisionError("storage_key_outside_managed_root")
        return f"{self._bucket}/{prefix}"

    def _validate_managed_key(self, key: str) -> str:
        """Require every S3 object operation to stay in this typed root."""
        self.namespace_for(key)
        return key

    def _validate_managed_prefix(self, prefix: str) -> str:
        managed = self._prefix()
        full_prefix = prefix or managed
        if not full_prefix.startswith(managed):
            raise StorageCollisionError("storage_key_outside_managed_root")
        return full_prefix

    def reclaim_unverified(
        self,
        key: str,
        *,
        expected_size: int,
        expected_etag: str | None,
        expected_sha256: str | None = None,
        expected_version_id: str | None = None,
    ) -> bool:
        self.namespace_for(key)
        if expected_version_id is not None:
            import botocore.exceptions

            try:
                response = self._client.head_object(
                    Bucket=self._bucket,
                    Key=key,
                    VersionId=expected_version_id,
                )
            except botocore.exceptions.ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in {
                    "404",
                    "NoSuchKey",
                    "NoSuchVersion",
                    "NotFound",
                }:
                    return True
                raise
            etag = response.get("ETag")
            if etag and not str(etag).startswith('"'):
                etag = f'"{etag}"'
            if int(response.get("ContentLength", 0) or 0) != expected_size:
                return False
            if expected_etag is not None and str(etag) != expected_etag:
                return False
            self._client.delete_object(
                Bucket=self._bucket,
                Key=key,
                VersionId=expected_version_id,
            )
            return True
        info = self.object_info(key)
        if info is None:
            return True
        if info.size != expected_size:
            return False
        if expected_etag is not None and info.etag != expected_etag:
            return False
        if expected_sha256 is not None:
            digest = hashlib.sha256()
            for chunk in self.stream_chunks(key):
                digest.update(chunk)
            if digest.hexdigest() != expected_sha256.lower():
                return False
        self._client.delete_object(Bucket=self._bucket, Key=key)
        return True

    def destructive_lifecycle_findings(self) -> list[dict[str, object]]:
        import botocore.exceptions

        try:
            response = self._client.get_bucket_lifecycle_configuration(
                Bucket=self._bucket
            )
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {
                "404",
                "NoSuchLifecycleConfiguration",
                "NoSuchLifecycle",
            }:
                return []
            logger.warning("S3 lifecycle audit failed", exc_info=True)
            return []
        managed_prefix = self._prefix()
        findings: list[dict[str, object]] = []
        for rule in response.get("Rules", []):
            if rule.get("Status") != "Enabled" or "Expiration" not in rule:
                continue
            filter_value = rule.get("Filter") or {}
            prefix = str(filter_value.get("Prefix", rule.get("Prefix", "")))
            if managed_prefix.startswith(prefix) or prefix.startswith(managed_prefix):
                findings.append(
                    {
                        "rule_id": str(rule.get("ID", "unnamed")),
                        "prefix": prefix,
                        "expiration": rule["Expiration"],
                    }
                )
        return findings

    def _ensure_bucket(self) -> None:
        import botocore.exceptions

        try:
            self._client.head_bucket(Bucket=self._bucket)
            logger.info("s3: bucket %r found", self._bucket)
        except botocore.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchBucket", "NotFound"):
                raise StorageConfigurationError(
                    f"S3 bucket {self._bucket!r} does not exist; create it with "
                    "your storage provider and grant PrintStash object access"
                ) from exc
            raise StorageConfigurationError(
                f"S3 bucket {self._bucket!r} is not accessible; verify the "
                "endpoint, region, credentials, and bucket permissions"
            ) from exc

    def _prefix(self) -> str:
        value = getattr(self, "_s3_root", None)
        if value is None:
            # Test doubles built with ``object.__new__`` predate the captured
            # identity. Real instances always take the immutable branch above.
            value = str(getattr(settings, "s3_root", "vault-data") or "vault-data")
        return f"{value}/"

    @staticmethod
    def _normalized_root(value: str) -> str:
        value = value.strip().strip("/")
        if (
            not value
            or value in {".", ".."}
            or any(part in {"", ".", ".."} for part in Path(value).parts)
        ):
            raise StorageConfigurationError("s3_root_invalid")
        return value

    def direct_path(self, key: str) -> Path | None:
        return None

    def blob_key(self, slug: str, version: int, filename: str) -> str:
        return f"{self._prefix()}files/{slug}/v{version}/{filename}"

    def thumbnail_key(self, file_id: int) -> str:
        return f"{self._prefix()}thumbs/{file_id}.webp"

    def source_cover_key(self, provenance_source_id: int) -> str:
        return f"{self._prefix()}source-covers/{provenance_source_id}.webp"

    def capture_upload_slot_key(self, slot_id: str) -> str:
        return f"{self._prefix()}capture-slots/{slot_id}"

    def legacy_thumbnail_key(self, file_id: int) -> str:
        return f"{self._prefix()}thumbs/{file_id}.png"

    def stl_cache_key(self, sha256: str) -> str:
        return f"{self._prefix()}stl-cache/{sha256}.stl"

    def collection_image_key(self, collection_id: int, name: str) -> str:
        return f"{self._prefix()}collection-images/{collection_id}/{name}"

    def document_file_key(self, document_id: int, name: str) -> str:
        return f"{self._prefix()}documents/{document_id}/{name}"

    def document_image_key(self, document_id: int, name: str) -> str:
        return f"{self._prefix()}document-images/{document_id}/{name}"

    def multipart_model_cover_key(self, multipart_model_id: int, name: str) -> str:
        return f"{self._prefix()}multipart-covers/{multipart_model_id}/{name}"

    def exists(self, key: str) -> bool:
        return self.object_info(key) is not None

    def object_info(self, key: str) -> StorageObjectInfo | None:
        import botocore.exceptions

        self._validate_managed_key(key)
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except botocore.exceptions.ClientError as exc:
            # Only a genuine "not there" is False. Credential, permission and
            # network errors must raise: callers need to distinguish an absent
            # object from a storage backend they cannot inspect.
            if exc.response.get("Error", {}).get("Code") in (
                "404",
                "NoSuchKey",
                "NotFound",
            ):
                return None
            raise
        etag = response.get("ETag")
        if etag and not str(etag).startswith('"'):
            etag = f'"{etag}"'
        return StorageObjectInfo(
            size=int(response.get("ContentLength", 0) or 0),
            etag=str(etag) if etag else None,
        )

    def write_stream(self, src: BinaryIO, key: str) -> int:
        return self.create_stream(src, key).size

    def write_bytes(self, data: bytes, key: str) -> int:
        return self.create_bytes(data, key).size

    def create_stream(self, src: BinaryIO, key: str) -> CreationReceipt:
        import botocore.exceptions

        self._validate_managed_key(key)
        if getattr(self, "_read_only", False):
            raise StorageConfigurationError("remote_storage_read_only")

        token = uuid.uuid4().hex
        threshold = int(settings.s3_multipart_threshold_mb) * 1024 * 1024
        spool = tempfile.SpooledTemporaryFile(max_size=threshold)
        shutil.copyfileobj(src, spool, length=1024 * 1024)
        size = spool.tell()
        spool.seek(0)
        try:
            if size > threshold:
                try:
                    response = self._multipart_create(spool, key=key, token=token)
                except botocore.exceptions.ParamValidationError:
                    spool.seek(0)
                    response = self._client.put_object(
                        Bucket=self._bucket,
                        Key=key,
                        Body=spool,
                        IfNoneMatch="*",
                        Metadata={"printstash-create-token": token},
                    )
            else:
                response = self._client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=spool,
                    IfNoneMatch="*",
                    Metadata={"printstash-create-token": token},
                )
        except botocore.exceptions.ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in {
                "412",
                "PreconditionFailed",
                "ConditionalRequestConflict",
            } or status in {409, 412}:
                raise StorageCollisionError(key) from exc
            raise
        finally:
            spool.close()
        info = self.object_info(key)
        if info is None:
            raise RuntimeError(f"storage create could not verify destination: {key}")
        etag = response.get("ETag") or info.etag
        return CreationReceipt(
            key=key,
            size=info.size,
            token=token,
            backend="s3",
            namespace=f"{self._bucket}/{self._prefix()}",
            etag=str(etag) if etag else None,
            version_id=(
                str(response["VersionId"]) if response.get("VersionId") else None
            ),
        )

    def _multipart_create(
        self,
        src: tempfile.SpooledTemporaryFile[bytes],
        *,
        key: str,
        token: str,
    ) -> dict:
        """Publish multipart data create-only, aborting every incomplete upload."""
        created = self._client.create_multipart_upload(
            Bucket=self._bucket,
            Key=key,
            Metadata={"printstash-create-token": token},
        )
        upload_id = created["UploadId"]
        parts: list[dict[str, object]] = []
        try:
            part_number = 1
            while chunk := src.read(8 * 1024 * 1024):
                uploaded = self._client.upload_part(
                    Bucket=self._bucket,
                    Key=key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    Body=chunk,
                )
                parts.append({"ETag": uploaded["ETag"], "PartNumber": part_number})
                part_number += 1
            return self._client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
                IfNoneMatch="*",
            )
        except Exception:
            try:
                self._client.abort_multipart_upload(
                    Bucket=self._bucket, Key=key, UploadId=upload_id
                )
            except Exception:
                logger.exception("S3 multipart abort failed", extra={"key": key})
            raise

    def rollback_create(self, receipt: CreationReceipt) -> bool:
        # Validate the opaque key before inspecting receipt metadata or making
        # any remote request.  A forged receipt from another typed root must
        # never be able to probe or delete that root's version.
        self._validate_managed_key(receipt.key)
        if not receipt.version_id:
            if self.object_info(receipt.key) is None:
                return True
            logger.warning(
                "storage delete blocked: S3 object has no immutable version identity",
                extra={"key": receipt.key},
            )
            return False
        import botocore.exceptions

        try:
            self._client.head_object(
                Bucket=self._bucket,
                Key=receipt.key,
                VersionId=receipt.version_id,
            )
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {
                "404",
                "NoSuchKey",
                "NoSuchVersion",
                "NotFound",
            }:
                return True
            raise
        if not self.creation_matches(receipt):
            return False
        kwargs = {
            "Bucket": self._bucket,
            "Key": receipt.key,
            "VersionId": receipt.version_id,
        }
        self._client.delete_object(**kwargs)
        return True

    def replace_stream(
        self, src: BinaryIO, receipt: CreationReceipt
    ) -> CreationReceipt:
        import botocore.exceptions

        if not receipt.etag or not self.creation_matches(receipt):
            raise StorageCollisionError(receipt.key)
        token = uuid.uuid4().hex
        try:
            response = self._client.put_object(
                Bucket=self._bucket,
                Key=receipt.key,
                Body=src,
                IfMatch=receipt.etag,
                Metadata={"printstash-create-token": token},
            )
        except botocore.exceptions.ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in {
                "412",
                "PreconditionFailed",
                "ConditionalRequestConflict",
            } or status_code in {
                409,
                412,
            }:
                raise StorageCollisionError(receipt.key) from exc
            raise
        info = self.object_info(receipt.key)
        if info is None:
            raise RuntimeError("storage_replace_verification_failed")
        etag = response.get("ETag") or info.etag
        return CreationReceipt(
            key=receipt.key,
            size=info.size,
            token=token,
            backend="s3",
            namespace=receipt.namespace,
            etag=str(etag) if etag else None,
            version_id=(
                str(response["VersionId"]) if response.get("VersionId") else None
            ),
        )

    def creation_matches(self, receipt: CreationReceipt) -> bool:
        if (
            receipt.backend != "s3"
            or receipt.namespace != f"{self._bucket}/{self._prefix()}"
        ):
            return False
        self._validate_managed_key(receipt.key)
        try:
            kwargs = {"Bucket": self._bucket, "Key": receipt.key}
            if receipt.version_id:
                kwargs["VersionId"] = receipt.version_id
            response = self._client.head_object(**kwargs)
        except Exception:
            raise
        metadata = response.get("Metadata", {})
        if metadata.get("printstash-create-token") != receipt.token:
            logger.warning(
                "storage rollback skipped: remote token no longer matches receipt",
                extra={"key": receipt.key},
            )
            return False
        if int(response.get("ContentLength", -1)) != receipt.size:
            return False
        if receipt.etag and str(response.get("ETag", "")) != receipt.etag:
            return False
        return True

    def adopt_existing(
        self, key: str, *, expected_size: int, expected_sha256: str
    ) -> CreationReceipt:
        """Recover a pending S3 publication with content and token proof."""
        import botocore.exceptions

        self._validate_managed_key(key)
        try:
            head = self._client.head_object(Bucket=self._bucket, Key=key)
        except botocore.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise FileNotFoundError(key) from exc
            raise
        size = int(head.get("ContentLength", -1))
        metadata = head.get("Metadata", {})
        token = metadata.get("printstash-create-token")
        if size != expected_size or not isinstance(token, str) or not token:
            raise StorageCollisionError(key)
        get_kwargs: dict[str, str] = {"Bucket": self._bucket, "Key": key}
        version_id = head.get("VersionId")
        if version_id:
            get_kwargs["VersionId"] = str(version_id)
        response = self._client.get_object(**get_kwargs)
        digest = hashlib.sha256(response["Body"].read()).hexdigest()
        if digest != expected_sha256.lower():
            raise StorageCollisionError(key)
        etag = head.get("ETag")
        if etag and not str(etag).startswith('"'):
            etag = f'"{etag}"'
        receipt = CreationReceipt(
            key=key,
            size=size,
            token=token,
            backend="s3",
            namespace=f"{self._bucket}/{self._prefix()}",
            etag=str(etag) if etag else None,
            version_id=str(version_id) if version_id else None,
        )
        if not self.creation_matches(receipt):
            raise StorageCollisionError(key)
        return receipt

    def verify_destructive_access(self, keys: list[str]) -> None:
        if not keys:
            return
        probe_key = f"{self._prefix()}.printstash-delete-probes/{uuid.uuid4().hex}"
        receipt = self.create_bytes(b"", probe_key)
        if not self.rollback_create(receipt):
            raise RuntimeError("storage_delete_probe_cleanup_unverified")

    def move(self, src_key: str, dest_key: str) -> None:
        del src_key, dest_key
        raise RuntimeError("unchecked_storage_move_disabled")

    def stat_size(self, key: str) -> int:
        import botocore.exceptions

        self._validate_managed_key(key)
        try:
            resp = self._client.head_object(Bucket=self._bucket, Key=key)
        except botocore.exceptions.ClientError as exc:
            _raise_s3_missing_object(exc, key)
        return resp.get("ContentLength", 0)

    def read_bytes(self, key: str) -> bytes:
        self._validate_managed_key(key)
        resp = self._client.get_object(Bucket=self._bucket, Key=key)
        return resp["Body"].read()

    def stream_chunks(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        import botocore.exceptions

        self._validate_managed_key(key)
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
        except botocore.exceptions.ClientError as exc:
            _raise_s3_missing_object(exc, key)
        body = resp["Body"]
        try:
            while True:
                chunk = body.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            body.close()

    def download_to_path(self, key: str, dest: Path) -> Path:
        self._validate_managed_key(key)
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return _copy_stream_create_only(response["Body"], dest)

    def upload_file(self, src: Path, key: str) -> None:
        with src.open("rb") as source:
            self.create_stream(source, key)

    def ensure_setup(self) -> None:
        self._ensure_bucket()
        self._probe_capabilities()

    def delete(self, key: str) -> None:
        del key
        raise RuntimeError("unchecked_storage_delete_disabled")

    def list_keys(self, prefix: str = "") -> list[str]:
        full_prefix = self._validate_managed_prefix(prefix)
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def walk_keys(self, prefix: str = "") -> Iterator[str]:
        full_prefix = self._validate_managed_prefix(prefix)
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]

    def usage(self, prefix: str = "") -> dict:
        full_prefix = self._validate_managed_prefix(prefix)
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

    def browser_download(
        self,
        key: str,
        filename: str,
        media_type: str,
        *,
        origin: str | None = None,
        inline: bool = False,
    ) -> BrowserDownload | None:
        """Sign one managed GET; a browser fetch also needs measured CORS policy."""
        from urllib.parse import urlsplit

        self._validate_managed_key(key)
        if self._endpoint_url and urlsplit(self._endpoint_url).scheme != "https":
            return None
        if origin is not None:
            try:
                rules = self._client.get_bucket_cors(Bucket=self._bucket)["CORSRules"]
            except Exception:
                # Lack of read-only CORS permission is not a delivery outage.
                return None
            # S3 uses the first matching rule; a later permissive rule does
            # not establish that this browser can read the response headers.
            permitted = False
            for rule in rules:
                if "GET" not in rule.get("AllowedMethods", []):
                    continue
                if not any(
                    re.fullmatch(re.escape(allowed).replace(r"\*", ".*"), origin)
                    for allowed in rule.get("AllowedOrigins", [])
                ):
                    continue
                exposed = {header.lower() for header in rule.get("ExposeHeaders", [])}
                allowed_headers = {
                    header.lower() for header in rule.get("AllowedHeaders", [])
                }
                permitted = bool(exposed & {"*", "content-disposition"}) and (
                    "*" in allowed_headers
                    or {
                        "cache-control",
                        "pragma",
                        "if-none-match",
                        "if-modified-since",
                        "range",
                        "if-range",
                    }.issubset(allowed_headers)
                )
                break
            if not permitted:
                return None
        info = self.object_info(key)
        if info is None:
            raise FileNotFoundError("file_blob_missing")
        params = {
            "Bucket": self._bucket,
            "Key": key,
            "ResponseContentDisposition": content_disposition(filename, inline=inline),
            "ResponseContentType": media_type,
            "ResponseCacheControl": "private, no-store",
        }
        if info.version_id and info.version_id != "null":
            params["VersionId"] = info.version_id
        ttl = min(60, int(settings.s3_presigned_url_expire_seconds))
        try:
            url = self._client.generate_presigned_url(
                "get_object", Params=params, ExpiresIn=ttl, HttpMethod="GET"
            )
        except Exception:
            # Never include SDK diagnostics: they can contain signed query data.
            logger.warning("S3 browser delivery signing unavailable")
            return None
        return BrowserDownload(
            url=url,
            key=key,
            expires_at=utcnow() + timedelta(seconds=ttl),
            cors_origin=origin,
            version_id=info.version_id,
        )

    @property
    def supports_ranges(self) -> bool:
        return True

    def stream_range(self, key: str, start: int, end: int) -> Iterator[bytes]:
        from botocore.exceptions import ClientError

        self._validate_managed_key(key)
        try:
            response = self._client.get_object(
                Bucket=self._bucket, Key=key, Range=f"bytes={start}-{end}"
            )
        except ClientError as exc:
            _raise_s3_missing_object(exc, key)
        body = response["Body"]
        if (
            not response.get("ContentRange", "").startswith(f"bytes {start}-{end}/")
            or response.get("ContentLength") != end - start + 1
        ):
            body.close()
            raise OperationError("storage_range_mismatch", kind=ErrorKind.UPSTREAM)
        return _RangeBody(body, end - start + 1)

    def delivery_diagnostics(self) -> dict:
        from urllib.parse import urlsplit

        candidate = (
            not self._endpoint_url or urlsplit(self._endpoint_url).scheme == "https"
        )
        return {
            "mode": "native_when_verified" if candidate else "proxy",
            "native_candidate": candidate,
            "cors": "verified_per_request",
            "maximum_url_seconds": 60,
            "ranges": True,
        }

    def health_probe(self) -> dict:
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return {
                "backend": "s3",
                "ok": True,
                "bucket": self._bucket,
                "endpoint": settings.s3_endpoint_url,
                "capabilities": self.capabilities.as_dict(),
                "diagnostics": self.probe_diagnostics,
            }
        except Exception as exc:
            return {
                "backend": "s3",
                "ok": False,
                "bucket": self._bucket,
                "endpoint": settings.s3_endpoint_url,
                "error": str(exc),
                "capabilities": self.capabilities.as_dict(),
                "diagnostics": self.probe_diagnostics,
            }
