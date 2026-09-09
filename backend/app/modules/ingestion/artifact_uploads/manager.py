"""Durable upload manager; routes never learn adapter-private state."""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, update
from sqlmodel import Session, select

from app.core.config import settings
from app.core.metrics import record_artifact_upload_bytes, record_artifact_upload_event
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    ArtifactUploadPart,
    ArtifactUploadSession,
    ArtifactUploadState,
    StagingLease,
    User,
)
from app.modules.storage.migration_identity import namespace_ref
from app.modules.storage.storage_backend.contracts import StorageBackend
from app.modules.storage.storage_backend.runtime import get_backend

from .api_chunks import CHUNK_SIZE, ApiChunkUploadAdapter
from .contracts import ChunkReceipt, UploadPlan, UploadRequest, VerifiedStagedArtifact
from .native_parts import NativeMultipartUploadAdapter
from .state import require_transition


class ArtifactUploadError(ValueError):
    pass


_ACTIVE = {
    ArtifactUploadState.CREATED,
    ArtifactUploadState.UPLOADING,
    ArtifactUploadState.VERIFYING,
    ArtifactUploadState.INGESTING,
}
_API_MODES = frozenset({"api_chunks", "simple"})
_NATIVE_PURPOSES = frozenset({"model", "gcode", "revision", "slicer"})


class SqlArtifactUploadManager:
    def __init__(
        self,
        session: Session,
        *,
        staging_root: Path | None = None,
        backend: StorageBackend | None = None,
    ) -> None:
        self.session = session
        self.api_adapter = ApiChunkUploadAdapter(
            staging_root or settings.incoming_dir / "artifact-uploads"
        )
        self.adapter = self.api_adapter
        if backend is None:
            try:
                backend = get_backend()
            except RuntimeError:
                backend = None
        self.native_adapter = (
            NativeMultipartUploadAdapter(backend, self.api_adapter.root)
            if backend is not None
            else None
        )

    def create(self, request: UploadRequest, actor: User) -> ArtifactUploadSession:
        if actor.id is None:
            raise ArtifactUploadError("artifact_upload_actor_invalid")
        if request.size_bytes <= 0 or request.size_bytes > settings.max_upload_bytes:
            raise ArtifactUploadError("artifact_upload_size_invalid")
        request_json = json.dumps(
            request.options, sort_keys=True, separators=(",", ":")
        )
        if request.options.get("_idempotency_key"):
            existing = self.session.exec(
                select(ArtifactUploadSession)
                .where(
                    ArtifactUploadSession.owner_user_id == actor.id,
                    ArtifactUploadSession.purpose == request.purpose,
                    ArtifactUploadSession.target_role == request.target_role,
                    ArtifactUploadSession.target_id == request.target_id,
                    ArtifactUploadSession.filename == request.filename,
                    ArtifactUploadSession.media_type == request.media_type,
                    ArtifactUploadSession.declared_size == request.size_bytes,
                    ArtifactUploadSession.client_sha256
                    == (
                        request.client_sha256.lower() if request.client_sha256 else None
                    ),
                    ArtifactUploadSession.request_json == request_json,
                    ArtifactUploadSession.state.in_(_ACTIVE),
                )
                .order_by(ArtifactUploadSession.created_at.desc())
            ).first()
            if existing is not None:
                return existing
        upload_id = uuid.uuid4().hex
        native = self.native_adapter
        capability = native.capability if native is not None else None
        use_native = (
            request.purpose in _NATIVE_PURPOSES
            and capability is not None
            and request.size_bytes > capability.part_size
        )
        adapter_id = (
            "native_parts"
            if use_native
            else "simple"
            if request.size_bytes <= CHUNK_SIZE
            else self.api_adapter.adapter_id
        )
        self._require_capacity(
            actor.id,
            size_bytes=request.size_bytes,
            adapter_id=adapter_id,
        )
        upload = ArtifactUploadSession(
            id=upload_id,
            owner_user_id=actor.id,
            purpose=request.purpose,
            target_role=request.target_role,
            target_id=request.target_id,
            request_json=request_json,
            filename=request.filename,
            media_type=request.media_type,
            declared_size=request.size_bytes,
            client_sha256=request.client_sha256.lower()
            if request.client_sha256
            else None,
            state=ArtifactUploadState.CREATED,
            adapter_id=adapter_id,
            destination_ref=(
                namespace_ref(native.backend)
                if use_native and native and native.backend.storage_target
                else None
            ),
            expires_at=utcnow() + timedelta(hours=settings.staging_import_lease_hours),
        )
        if use_native and native is not None:
            try:
                upload.protected_native_id = native.begin(upload)
            except Exception:
                # A backend that cannot initiate the required checksum-scoped
                # operation is not native-capable for this session.
                fallback_id = (
                    "simple"
                    if request.size_bytes <= CHUNK_SIZE
                    else self.api_adapter.adapter_id
                )
                self._require_capacity(
                    actor.id,
                    size_bytes=request.size_bytes,
                    adapter_id=fallback_id,
                )
                upload.adapter_id = fallback_id
                upload.destination_ref = None
                self.api_adapter.session_directory(upload.id, create=True)
        else:
            self.api_adapter.session_directory(upload.id, create=True)
        try:
            self.session.add(upload)
            self.session.commit()
            self.session.refresh(upload)
            record_artifact_upload_event("created", upload.adapter_id)
        except Exception:
            self.session.rollback()
            self.adapter_for(upload).abort_owned(upload)
            raise
        return upload

    def _require_capacity(
        self, owner_user_id: int, *, size_bytes: int, adapter_id: str
    ) -> None:
        """Reserve the worst-case local footprint before a session accepts bytes."""

        pre_ingestion = self.session.exec(
            select(ArtifactUploadSession).where(
                ArtifactUploadSession.state.in_(
                    {
                        ArtifactUploadState.CREATED,
                        ArtifactUploadState.UPLOADING,
                        ArtifactUploadState.VERIFYING,
                    }
                )
            )
        ).all()
        ingesting_api = self.session.exec(
            select(ArtifactUploadSession).where(
                ArtifactUploadSession.state == ArtifactUploadState.INGESTING,
                ArtifactUploadSession.adapter_id.in_(_API_MODES),
            )
        ).all()
        lease_count, leased_bytes = self.session.exec(
            select(
                func.count(StagingLease.id),
                func.coalesce(func.sum(StagingLease.size_bytes), 0),
            )
        ).one()
        owner_leases = self.session.exec(
            select(func.count(StagingLease.id)).where(
                StagingLease.owner_user_id == owner_user_id
            )
        ).one()
        pending_count = len(pre_ingestion) + int(lease_count)
        owner_count = sum(
            item.owner_user_id == owner_user_id for item in pre_ingestion
        ) + int(owner_leases)

        def reserved(upload: ArtifactUploadSession) -> int:
            multiplier = 2 if upload.adapter_id in _API_MODES else 1
            return upload.declared_size * multiplier

        reserved_bytes = (
            int(leased_bytes)
            + sum(reserved(upload) for upload in pre_ingestion)
            + sum(upload.declared_size for upload in ingesting_api)
        )
        required_bytes = size_bytes * (2 if adapter_id in _API_MODES else 1)
        outstanding_bytes = sum(
            max(0, reserved(upload) - upload.received_bytes) for upload in pre_ingestion
        )
        self.api_adapter.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        free_bytes = shutil.disk_usage(self.api_adapter.root).free
        if (
            pending_count >= settings.staging_max_pending
            or owner_count >= settings.staging_max_active_per_user
            or reserved_bytes + required_bytes > settings.staging_max_gb * 1024**3
            or free_bytes
            < settings.staging_min_free_gb * 1024**3
            + outstanding_bytes
            + required_bytes
        ):
            raise ArtifactUploadError("staging_capacity_exceeded")

    def get(self, session_id: str, actor: User) -> ArtifactUploadSession:
        upload = self.session.get(ArtifactUploadSession, session_id)
        if upload is None or (
            upload.owner_user_id != actor.id and not actor.is_superuser
        ):
            raise ArtifactUploadError("artifact_upload_not_found")
        return upload

    def parts(self, upload: ArtifactUploadSession) -> list[ArtifactUploadPart]:
        return list(
            self.session.exec(
                select(ArtifactUploadPart)
                .where(ArtifactUploadPart.session_id == upload.id)
                .order_by(ArtifactUploadPart.part_number)
            )
        )

    def plan(self, session_id: str, actor: User) -> UploadPlan:
        upload = self.get(session_id, actor)
        if (
            upload.state == ArtifactUploadState.FAILED
            and upload.retryable
            and ensure_utc(upload.expires_at) > utcnow()
        ):
            self.transition(upload, ArtifactUploadState.UPLOADING)
        else:
            self._require_active(upload)
        if upload.received_bytes > 0:
            record_artifact_upload_event("resumed", upload.adapter_id)
        if upload.adapter_id == "native_parts":
            adapter = self._native_for(upload)
            capability = adapter.capability
            if capability is None:
                raise ArtifactUploadError("native_upload_capability_unavailable")
            return UploadPlan(
                mode="native_parts",
                chunk_size=capability.part_size,
                max_parallel=3,
                upload_path=f"/api/v1/artifact-uploads/{upload.id}/parts/{{part_number}}",
            )
        return UploadPlan(
            mode=upload.adapter_id,
            chunk_size=CHUNK_SIZE,
            max_parallel=3,
            upload_path=f"/api/v1/artifact-uploads/{upload.id}/chunks/{{index}}",
        )

    def record_chunk(
        self,
        session_id: str,
        receipt: ChunkReceipt,
        payload: bytes,
        actor: User,
    ) -> ArtifactUploadPart:
        upload = self.get(session_id, actor)
        self._require_active(upload)
        if upload.adapter_id not in _API_MODES:
            raise ArtifactUploadError("artifact_upload_mode_conflict")
        if upload.state not in {
            ArtifactUploadState.CREATED,
            ArtifactUploadState.UPLOADING,
        }:
            raise ArtifactUploadError("artifact_upload_state_conflict")
        existing = self.session.exec(
            select(ArtifactUploadPart).where(
                ArtifactUploadPart.session_id == upload.id,
                ArtifactUploadPart.part_number == receipt.index + 1,
            )
        ).first()
        if existing is not None:
            if (
                existing.byte_offset,
                existing.size_bytes,
                existing.sha256,
            ) != (receipt.offset, receipt.size_bytes, receipt.sha256.lower()):
                raise ArtifactUploadError("artifact_upload_chunk_conflict")
            self.api_adapter.write_chunk(upload, receipt, payload)
            return existing

        previous_received = sum(item.size_bytes for item in self.parts(upload))
        self.api_adapter.write_chunk(upload, receipt, payload)
        part = ArtifactUploadPart(
            session_id=upload.id,
            part_number=receipt.index + 1,
            byte_offset=receipt.offset,
            size_bytes=receipt.size_bytes,
            sha256=receipt.sha256.lower(),
        )
        self.session.add(part)
        self.transition(
            upload,
            ArtifactUploadState.UPLOADING,
            received_bytes=previous_received + receipt.size_bytes,
        )
        record_artifact_upload_bytes(
            upload.adapter_id, receipt.size_bytes, bypassed_api=False
        )
        self.session.refresh(part)
        return part

    def sign_native_part(
        self,
        session_id: str,
        *,
        part_number: int,
        checksum_sha256: str,
        actor: User,
    ) -> str:
        upload = self.get(session_id, actor)
        self._require_active(upload)
        if upload.state not in {
            ArtifactUploadState.CREATED,
            ArtifactUploadState.UPLOADING,
        }:
            raise ArtifactUploadError("artifact_upload_state_conflict")
        return self._native_for(upload).sign_part(
            upload, part_number=part_number, checksum_sha256=checksum_sha256
        )

    def record_native_part(
        self,
        session_id: str,
        *,
        part_number: int,
        size_bytes: int,
        checksum_sha256: str,
        etag: str,
        actor: User,
    ) -> ArtifactUploadPart:
        upload = self.get(session_id, actor)
        self._require_active(upload)
        if upload.state not in {
            ArtifactUploadState.CREATED,
            ArtifactUploadState.UPLOADING,
        }:
            raise ArtifactUploadError("artifact_upload_state_conflict")
        adapter = self._native_for(upload)
        adapter.validate_receipt(
            upload,
            part_number=part_number,
            size_bytes=size_bytes,
            checksum_sha256=checksum_sha256,
            etag=etag,
        )
        existing = self.session.exec(
            select(ArtifactUploadPart).where(
                ArtifactUploadPart.session_id == upload.id,
                ArtifactUploadPart.part_number == part_number,
            )
        ).first()
        receipt_json = json.dumps({"etag": etag}, separators=(",", ":"))
        capability = adapter.capability
        assert capability is not None
        offset = (part_number - 1) * capability.part_size
        if existing is not None:
            if (
                existing.byte_offset,
                existing.size_bytes,
                existing.sha256,
                existing.provider_receipt_json,
            ) != (offset, size_bytes, checksum_sha256.lower(), receipt_json):
                raise ArtifactUploadError("artifact_upload_chunk_conflict")
            return existing
        previous_received = sum(item.size_bytes for item in self.parts(upload))
        part = ArtifactUploadPart(
            session_id=upload.id,
            part_number=part_number,
            byte_offset=offset,
            size_bytes=size_bytes,
            sha256=checksum_sha256.lower(),
            provider_receipt_json=receipt_json,
        )
        self.session.add(part)
        self.transition(
            upload,
            ArtifactUploadState.UPLOADING,
            received_bytes=previous_received + size_bytes,
        )
        record_artifact_upload_bytes(upload.adapter_id, size_bytes, bypassed_api=True)
        self.session.refresh(part)
        return part

    def finalize(
        self, session_id: str, actor: User
    ) -> tuple[ArtifactUploadSession, VerifiedStagedArtifact]:
        upload = self.get(session_id, actor)
        self._require_active(upload)
        adapter = self.adapter_for(upload)
        if upload.state == ArtifactUploadState.VERIFYING and upload.verified_sha256:
            verified = self.api_adapter._verified_existing(
                self.api_adapter.session_directory(upload.id) / "assembled.upload",
                upload,
            )
            return upload, verified
        if upload.state == ArtifactUploadState.UPLOADING:
            self.transition(
                upload,
                ArtifactUploadState.VERIFYING,
                error_code="artifact_upload_verification_active",
                retryable=False,
            )
        elif (
            upload.state == ArtifactUploadState.VERIFYING
            and upload.error_code == "artifact_upload_verification_interrupted"
            and upload.retryable
        ):
            self.transition(
                upload,
                ArtifactUploadState.VERIFYING,
                error_code="artifact_upload_verification_active",
                retryable=False,
            )
        else:
            raise ArtifactUploadError("artifact_upload_state_conflict")
        try:
            if isinstance(adapter, NativeMultipartUploadAdapter):

                def persist_completion(protected: str) -> None:
                    self.transition(
                        upload,
                        ArtifactUploadState.VERIFYING,
                        protected_native_id=protected,
                    )

                verified = adapter.complete(
                    upload,
                    self.parts(upload),
                    persist_completion=persist_completion,
                )
            else:
                verified = self.api_adapter.assemble(upload)
        except Exception as exc:
            record_artifact_upload_event("verification_failed", upload.adapter_id)
            record_artifact_upload_event("failed", upload.adapter_id)
            code = (
                str(exc).split(":", 1)[0][:128] or "artifact_upload_verification_failed"
            )
            self.transition(
                upload,
                ArtifactUploadState.FAILED,
                error_code=code,
                retryable=False,
            )
            raise
        self.transition(
            upload,
            ArtifactUploadState.VERIFYING,
            verified_size=verified.size_bytes,
            verified_sha256=verified.sha256,
            staging_identity_json=json.dumps(
                {
                    "device": verified.device,
                    "inode": verified.inode,
                    "ctime_ns": verified.ctime_ns,
                },
                sort_keys=True,
            ),
            error_code=None,
            retryable=False,
        )
        return upload, verified

    def abort(self, session_id: str, actor: User) -> ArtifactUploadSession:
        upload = self.get(session_id, actor)
        if upload.state == ArtifactUploadState.ABORTED:
            return upload
        if upload.state in {ArtifactUploadState.COMPLETED, ArtifactUploadState.EXPIRED}:
            raise ArtifactUploadError("artifact_upload_state_conflict")
        self.adapter_for(upload).abort_owned(upload)
        for part in self.parts(upload):
            self.session.delete(part)
        self.transition(upload, ArtifactUploadState.ABORTED)
        return upload

    def adapter_for(
        self, upload: ArtifactUploadSession
    ) -> ApiChunkUploadAdapter | NativeMultipartUploadAdapter:
        if upload.adapter_id in _API_MODES:
            return self.api_adapter
        return self._native_for(upload)

    def _native_for(
        self, upload: ArtifactUploadSession
    ) -> NativeMultipartUploadAdapter:
        if upload.adapter_id != "native_parts" or self.native_adapter is None:
            raise ArtifactUploadError("native_upload_capability_unavailable")
        target = self.native_adapter.backend.storage_target
        current_refs = (
            {target.target_ref, namespace_ref(self.native_adapter.backend)}
            if target
            else set()
        )
        if upload.destination_ref and (
            upload.destination_ref not in current_refs
            or upload.error_code == "artifact_upload_vault_generation_changed"
        ):
            from app.modules.storage.migration_uploads import retained_upload_backend

            backend = retained_upload_backend(self.session, upload.destination_ref)
            return NativeMultipartUploadAdapter(backend, self.native_adapter.local_root)
        return self.native_adapter

    def transition(
        self,
        upload: ArtifactUploadSession,
        target: ArtifactUploadState,
        **changes: object,
    ) -> ArtifactUploadSession:
        """Commit one legal state edge only if its durable version is unchanged."""

        current = ArtifactUploadState(upload.state)
        require_transition(current, target, retryable=upload.retryable)
        expected_version = upload.version
        result = self.session.execute(
            update(ArtifactUploadSession)
            .where(
                ArtifactUploadSession.id == upload.id,
                ArtifactUploadSession.version == expected_version,
                ArtifactUploadSession.state == current.value,
            )
            .values(
                state=target.value,
                version=expected_version + 1,
                updated_at=utcnow(),
                **changes,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            self.session.rollback()
            raise ArtifactUploadError("artifact_upload_state_conflict")
        self.session.commit()
        self.session.refresh(upload)
        return upload

    @staticmethod
    def _require_active(upload: ArtifactUploadSession) -> None:
        if ensure_utc(upload.expires_at) <= utcnow():
            raise ArtifactUploadError("artifact_upload_expired")
        if upload.state not in _ACTIVE:
            raise ArtifactUploadError("artifact_upload_state_conflict")
