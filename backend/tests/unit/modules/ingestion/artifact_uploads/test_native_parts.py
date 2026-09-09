"""Defend encrypted native-part receipts, completion recovery, and cleanup.

A failure here means provider-owned multipart bytes could be trusted or reclaimed
without the durable checksum and ownership evidence required by the upload plan.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path

import pytest

import app.modules.ingestion.artifact_uploads.native_parts as native_parts
from app.core.time import utcnow
from app.db.models import ArtifactUploadPart, ArtifactUploadSession
from app.modules.ingestion.artifact_uploads.native_parts import (
    NativeMultipartError,
    NativeMultipartUploadAdapter,
)
from app.modules.storage.storage_backend.contracts import (
    CreationReceipt,
    NativeMultipartCapability,
    NativeMultipartHandle,
    NativeMultipartPart,
)


def _upload(payload: bytes = b"abcdefgh") -> ArtifactUploadSession:
    return ArtifactUploadSession(
        id="native-unit-upload",
        owner_user_id=1,
        purpose="model",
        target_role="new_model",
        filename="part.stl",
        media_type="model/stl",
        declared_size=len(payload),
        client_sha256=hashlib.sha256(payload).hexdigest(),
        adapter_id="native_parts",
        expires_at=utcnow() + timedelta(hours=1),
    )


def _part(number: int, payload: bytes) -> ArtifactUploadPart:
    return ArtifactUploadPart(
        session_id="native-unit-upload",
        part_number=number,
        byte_offset=(number - 1) * 4,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        provider_receipt_json=json.dumps({"etag": f'"part-{number}"'}),
    )


class _NativeBackend:
    native_multipart_capability = NativeMultipartCapability(part_size=4, max_parts=10)

    def __init__(self, payload: bytes = b"abcdefgh") -> None:
        self.payload = payload
        self.handle = NativeMultipartHandle("private/staging/upload", "provider-id", "token")
        self.remote_parts: list[NativeMultipartPart] = []
        self.receipt = CreationReceipt(
            key="private/staging/upload",
            size=len(payload),
            token="token",
            backend="s3",
            namespace="bucket/private/",
            etag='"completed"',
        )
        self.complete_error: Exception | None = None
        self.recovered: CreationReceipt | None = None
        self.aborted = False
        self.rollback_result = True
        self.reclaim_result = False

    def begin_native_multipart(self, **_kwargs: object) -> NativeMultipartHandle:
        return self.handle

    def sign_native_multipart_part(self, *_args: object, **_kwargs: object) -> str:
        return "https://objects.test/signed"

    def list_native_multipart_parts(self, _handle: NativeMultipartHandle):
        return self.remote_parts

    def complete_native_multipart(self, *_args: object) -> CreationReceipt:
        if self.complete_error is not None:
            raise self.complete_error
        return self.receipt

    def recover_native_multipart_completion(self, *_args: object, **_kwargs: object):
        return self.recovered

    def download_to_path(self, _key: str, destination: Path) -> None:
        destination.write_bytes(self.payload)

    def abort_native_multipart(self, _handle: NativeMultipartHandle) -> None:
        self.aborted = True

    def rollback_create(self, _receipt: CreationReceipt) -> bool:
        return self.rollback_result

    def reclaim_unverified(self, *_args: object, **_kwargs: object) -> bool:
        return self.reclaim_result


@pytest.fixture
def cleartext_protection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(native_parts, "encrypt_secret", lambda value: value)
    monkeypatch.setattr(native_parts, "decrypt_secret", lambda value: value)


class TestNativeMultipartUploadAdapter:
    def test_completes_verified_provider_parts(
        self, tmp_path: Path, cleartext_protection: None
    ) -> None:
        payload = b"abcdefgh"
        backend = _NativeBackend(payload)
        upload = _upload(payload)
        adapter = NativeMultipartUploadAdapter(backend, tmp_path)  # type: ignore[arg-type]
        upload.protected_native_id = adapter.begin(upload)
        assert (
            adapter.sign_part(upload, part_number=1, checksum_sha256="a" * 64)
            == "https://objects.test/signed"
        )
        parts = [_part(1, b"abcd"), _part(2, b"efgh")]
        backend.remote_parts = [
            NativeMultipartPart(
                part.part_number,
                part.size_bytes,
                part.sha256,
                json.loads(part.provider_receipt_json or "{}")["etag"],
            )
            for part in parts
        ]

        def persist(value: str) -> None:
            upload.protected_native_id = value

        verified = adapter.complete(upload, parts, persist_completion=persist)

        assert verified.materialize().read_bytes() == payload
        protected = json.loads(upload.protected_native_id or "{}")
        assert protected["completion_receipt"]["etag"] == backend.receipt.etag

    def test_recovers_an_uncertain_provider_completion(
        self, tmp_path: Path, cleartext_protection: None
    ) -> None:
        backend = _NativeBackend()
        backend.complete_error = TimeoutError("completion response lost")
        backend.recovered = backend.receipt
        upload = _upload()
        adapter = NativeMultipartUploadAdapter(backend, tmp_path)  # type: ignore[arg-type]
        upload.protected_native_id = adapter.begin(upload)
        parts = [_part(1, b"abcd"), _part(2, b"efgh")]
        backend.remote_parts = [
            NativeMultipartPart(part.part_number, part.size_bytes, part.sha256, f'"part-{part.part_number}"')
            for part in parts
        ]

        verified = adapter.complete(upload, parts, persist_completion=lambda _value: None)

        assert verified.sha256 == upload.client_sha256

    @pytest.mark.parametrize(
        ("field", "value", "code"),
        [
            ("part_number", 3, "native_upload_part_invalid"),
            ("size_bytes", 3, "native_upload_part_size_invalid"),
            ("checksum_sha256", "not-a-hash", "native_upload_checksum_invalid"),
            ("etag", "bad\nreceipt", "native_upload_receipt_invalid"),
        ],
    )
    def test_rejects_invalid_durable_receipts(
        self,
        tmp_path: Path,
        field: str,
        value: object,
        code: str,
    ) -> None:
        adapter = NativeMultipartUploadAdapter(_NativeBackend(), tmp_path)  # type: ignore[arg-type]
        kwargs: dict[str, object] = {
            "part_number": 1,
            "size_bytes": 4,
            "checksum_sha256": "a" * 64,
            "etag": '"part-1"',
        }
        kwargs[field] = value

        with pytest.raises(NativeMultipartError, match=code):
            adapter.validate_receipt(_upload(), **kwargs)  # type: ignore[arg-type]

    def test_rejects_unavailable_native_capability(self, tmp_path: Path) -> None:
        backend = _NativeBackend()
        backend.native_multipart_capability = None
        adapter = NativeMultipartUploadAdapter(backend, tmp_path)  # type: ignore[arg-type]

        with pytest.raises(NativeMultipartError, match="capability_unavailable"):
            adapter.sign_part(_upload(), part_number=1, checksum_sha256="a" * 64)
        with pytest.raises(NativeMultipartError, match="capability_unavailable"):
            adapter.validate_receipt(
                _upload(),
                part_number=1,
                size_bytes=4,
                checksum_sha256="a" * 64,
                etag='"part-1"',
            )

    def test_rejects_unprotected_or_invalid_provider_identity(
        self, tmp_path: Path, cleartext_protection: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        adapter = NativeMultipartUploadAdapter(_NativeBackend(), tmp_path)  # type: ignore[arg-type]
        upload = _upload()
        monkeypatch.setattr(native_parts, "encrypt_secret", lambda _value: None)
        with pytest.raises(NativeMultipartError, match="protection_failed"):
            adapter.begin(upload)

        for protected in ("not-json", "[]", "{}"):
            upload.protected_native_id = protected
            with pytest.raises(NativeMultipartError, match="identity_invalid"):
                adapter.handle(upload)

    def test_aborts_both_owned_operation_forms(
        self, tmp_path: Path, cleartext_protection: None
    ) -> None:
        backend = _NativeBackend()
        adapter = NativeMultipartUploadAdapter(backend, tmp_path)  # type: ignore[arg-type]
        upload = _upload()
        upload.protected_native_id = adapter.begin(upload)
        adapter.abort_owned(upload)
        assert backend.aborted is True

        directory = tmp_path / upload.id
        directory.mkdir()
        (directory / "assembled.upload").write_bytes(b"payload")
        (directory / ".native-stale").write_bytes(b"partial")
        protected = asdict(backend.handle) | {"completion_receipt": asdict(backend.receipt)}
        upload.protected_native_id = json.dumps(protected)
        adapter.abort_owned(upload)
        assert not directory.exists()

        backend.rollback_result = False
        with pytest.raises(OSError, match="cleanup_unproven"):
            adapter.abort_owned(upload)
