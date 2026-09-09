"""Capability-driven native multipart transfer without leaking provider state."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from app.core.secrets import decrypt_secret, encrypt_secret
from app.db.models import ArtifactUploadPart, ArtifactUploadSession
from app.modules.storage.storage_backend.contracts import (
    CreationReceipt,
    NativeMultipartHandle,
    NativeMultipartPart,
    StorageBackend,
)

from .contracts import VerifiedStagedArtifact


class NativeMultipartError(ValueError):
    pass


class NativeMultipartUploadAdapter:
    adapter_id = "native_parts"

    def __init__(self, backend: StorageBackend, local_root: Path) -> None:
        self.backend = backend
        self.local_root = local_root

    @property
    def capability(self):
        return self.backend.native_multipart_capability

    def begin(self, upload: ArtifactUploadSession) -> str:
        handle = self.backend.begin_native_multipart(
            session_id=upload.id,
            filename=upload.filename,
            media_type=upload.media_type,
        )
        protected = encrypt_secret(json.dumps(asdict(handle), separators=(",", ":")))
        if protected is None:
            raise NativeMultipartError("native_upload_protection_failed")
        return protected

    @staticmethod
    def _decode(upload: ArtifactUploadSession) -> dict[str, object]:
        try:
            value = decrypt_secret(upload.protected_native_id)
            payload = json.loads(value or "")
        except (TypeError, ValueError) as exc:
            raise NativeMultipartError("native_upload_identity_invalid") from exc
        if not isinstance(payload, dict):
            raise NativeMultipartError("native_upload_identity_invalid")
        return payload

    def handle(self, upload: ArtifactUploadSession) -> NativeMultipartHandle:
        payload = self._decode(upload)
        try:
            return NativeMultipartHandle(
                key=str(payload["key"]),
                upload_id=str(payload["upload_id"]),
                ownership_token=str(payload["ownership_token"]),
            )
        except KeyError as exc:
            raise NativeMultipartError("native_upload_identity_invalid") from exc

    @classmethod
    def completion_receipt(
        cls, upload: ArtifactUploadSession
    ) -> CreationReceipt | None:
        """Read the protected, positively acknowledged native object identity."""
        if not upload.protected_native_id:
            return None
        completion = cls._decode(upload).get("completion_receipt")
        return CreationReceipt(**completion) if isinstance(completion, dict) else None

    @classmethod
    def relocate_completion(
        cls, upload: ArtifactUploadSession, receipt: CreationReceipt
    ) -> None:
        """Move an already completed staging object in an atomic Vault activation."""
        protected = cls._decode(upload)
        protected["completion_receipt"] = asdict(receipt)
        upload.protected_native_id = encrypt_secret(
            json.dumps(protected, separators=(",", ":"))
        )

    def sign_part(
        self,
        upload: ArtifactUploadSession,
        *,
        part_number: int,
        checksum_sha256: str,
    ) -> str:
        capability = self.capability
        if capability is None:
            raise NativeMultipartError("native_upload_capability_unavailable")
        total = (
            upload.declared_size + capability.part_size - 1
        ) // capability.part_size
        if not 1 <= part_number <= min(total, capability.max_parts):
            raise NativeMultipartError("native_upload_part_invalid")
        return self.backend.sign_native_multipart_part(
            self.handle(upload),
            part_number=part_number,
            checksum_sha256=checksum_sha256,
            expires_seconds=60,
        )

    def validate_receipt(
        self,
        upload: ArtifactUploadSession,
        *,
        part_number: int,
        size_bytes: int,
        checksum_sha256: str,
        etag: str,
    ) -> None:
        capability = self.capability
        if capability is None:
            raise NativeMultipartError("native_upload_capability_unavailable")
        total = (
            upload.declared_size + capability.part_size - 1
        ) // capability.part_size
        if not 1 <= part_number <= min(total, capability.max_parts):
            raise NativeMultipartError("native_upload_part_invalid")
        expected = (
            capability.part_size
            if part_number < total
            else upload.declared_size - capability.part_size * (total - 1)
        )
        if size_bytes != expected:
            raise NativeMultipartError("native_upload_part_size_invalid")
        if len(checksum_sha256) != 64 or any(
            char not in "0123456789abcdefABCDEF" for char in checksum_sha256
        ):
            raise NativeMultipartError("native_upload_checksum_invalid")
        if not etag or len(etag) > 256 or any(ord(char) < 32 for char in etag):
            raise NativeMultipartError("native_upload_receipt_invalid")

    def complete(
        self,
        upload: ArtifactUploadSession,
        receipts: list[ArtifactUploadPart],
        *,
        persist_completion: Callable[[str], None],
    ) -> VerifiedStagedArtifact:
        protected = self._decode(upload)
        completion = protected.get("completion_receipt")
        if isinstance(completion, dict):
            receipt = CreationReceipt(**completion)
        else:
            handle = self.handle(upload)
            durable = [
                NativeMultipartPart(
                    part_number=part.part_number,
                    size_bytes=part.size_bytes,
                    checksum_sha256=part.sha256,
                    etag=str(
                        json.loads(part.provider_receipt_json or "{}").get("etag", "")
                    ),
                )
                for part in receipts
            ]
            try:
                remote = self.backend.list_native_multipart_parts(handle)
                if remote != durable:
                    raise NativeMultipartError("native_upload_receipts_mismatch")
                receipt = self.backend.complete_native_multipart(handle, remote)
            except NativeMultipartError:
                raise
            except Exception:
                recovered = self.backend.recover_native_multipart_completion(
                    handle,
                    expected_size=upload.declared_size,
                    expected_sha256=upload.client_sha256,
                )
                if recovered is None:
                    raise
                receipt = recovered
            protected["completion_receipt"] = asdict(receipt)
            encoded = encrypt_secret(json.dumps(protected, separators=(",", ":")))
            if encoded is None:
                raise NativeMultipartError("native_upload_protection_failed")
            persist_completion(encoded)
        if receipt.size != upload.declared_size:
            raise NativeMultipartError("artifact_upload_size_mismatch")

        directory = self.local_root / upload.id
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary_name = tempfile.mkstemp(prefix=".native-", dir=directory)
        os.close(fd)
        temporary = Path(temporary_name)
        # A relocated native completion can now be backed by Local storage,
        # whose download contract is create-only. Reserve a unique private
        # name, then remove only our empty placeholder before materialization.
        temporary.unlink()
        destination = directory / "assembled.upload"
        try:
            self.backend.download_to_path(receipt.key, temporary)
            digest = hashlib.sha256()
            size = 0
            with temporary.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
            sha256 = digest.hexdigest()
            if size != upload.declared_size or (
                upload.client_sha256 and sha256 != upload.client_sha256.lower()
            ):
                raise NativeMultipartError("artifact_upload_hash_mismatch")
            os.link(temporary, destination)
            temporary.unlink(missing_ok=True)
            stat = destination.stat(follow_symlinks=False)
            return VerifiedStagedArtifact(
                path=destination,
                size_bytes=size,
                sha256=sha256,
                filename=upload.filename,
                device=stat.st_dev,
                inode=stat.st_ino,
                ctime_ns=stat.st_ctime_ns,
            )
        finally:
            temporary.unlink(missing_ok=True)

    def abort_owned(self, upload: ArtifactUploadSession) -> None:
        payload = self._decode(upload)
        completion = payload.get("completion_receipt")
        if isinstance(completion, dict):
            receipt = CreationReceipt(**completion)
            reclaimed = self.backend.rollback_create(receipt)
            if not reclaimed:
                reclaimed = self.backend.reclaim_unverified(
                    receipt.key,
                    expected_size=receipt.size,
                    expected_etag=receipt.etag,
                    expected_sha256=upload.verified_sha256,
                    expected_version_id=receipt.version_id,
                )
            if not reclaimed:
                raise OSError("native_upload_cleanup_unproven")
        else:
            self.backend.abort_native_multipart(self.handle(upload))
        directory = self.local_root / upload.id
        if directory.is_dir():
            (directory / "assembled.upload").unlink(missing_ok=True)
            for temporary in directory.glob(".native-*"):
                temporary.unlink(missing_ok=True)
            directory.rmdir()
