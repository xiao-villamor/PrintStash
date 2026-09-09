"""Durable upload manager; routes never learn adapter-private state."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, update
from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    ArtifactUploadPart,
    ArtifactUploadSession,
    ArtifactUploadState,
    User,
)

from .api_chunks import CHUNK_SIZE, ApiChunkUploadAdapter
from .contracts import ChunkReceipt, UploadPlan, UploadRequest, VerifiedStagedArtifact
from .state import require_transition


class ArtifactUploadError(ValueError):
    pass


_ACTIVE = {
    ArtifactUploadState.CREATED,
    ArtifactUploadState.UPLOADING,
    ArtifactUploadState.VERIFYING,
    ArtifactUploadState.INGESTING,
}


class SqlArtifactUploadManager:
    def __init__(self, session: Session, *, staging_root: Path | None = None) -> None:
        self.session = session
        self.adapter = ApiChunkUploadAdapter(
            staging_root or settings.incoming_dir / "artifact-uploads"
        )

    def create(self, request: UploadRequest, actor: User) -> ArtifactUploadSession:
        if actor.id is None:
            raise ArtifactUploadError("artifact_upload_actor_invalid")
        if request.size_bytes <= 0 or request.size_bytes > settings.max_upload_bytes:
            raise ArtifactUploadError("artifact_upload_size_invalid")
        active = self.session.exec(
            select(func.count(ArtifactUploadSession.id)).where(
                ArtifactUploadSession.owner_user_id == actor.id,
                ArtifactUploadSession.state.in_(_ACTIVE),
            )
        ).one()
        if int(active) >= settings.staging_max_active_per_user:
            raise ArtifactUploadError("staging_capacity_exceeded")

        upload = ArtifactUploadSession(
            id=uuid.uuid4().hex,
            owner_user_id=actor.id,
            purpose=request.purpose,
            target_role=request.target_role,
            target_id=request.target_id,
            request_json=json.dumps(
                request.options, sort_keys=True, separators=(",", ":")
            ),
            filename=request.filename,
            media_type=request.media_type,
            declared_size=request.size_bytes,
            client_sha256=request.client_sha256.lower()
            if request.client_sha256
            else None,
            state=ArtifactUploadState.CREATED,
            adapter_id=self.adapter.adapter_id,
            expires_at=utcnow() + timedelta(hours=settings.staging_import_lease_hours),
        )
        self.adapter.session_directory(upload.id, create=True)
        try:
            self.session.add(upload)
            self.session.commit()
            self.session.refresh(upload)
        except Exception:
            self.session.rollback()
            self.adapter.abort_owned(upload)
            raise
        return upload

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
        self._require_active(upload)
        return UploadPlan(
            mode="api_chunks",
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
            self.adapter.write_chunk(upload, receipt, payload)
            return existing

        previous_received = sum(item.size_bytes for item in self.parts(upload))
        self.adapter.write_chunk(upload, receipt, payload)
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
        self.session.refresh(part)
        return part

    def finalize(
        self, session_id: str, actor: User
    ) -> tuple[ArtifactUploadSession, VerifiedStagedArtifact]:
        upload = self.get(session_id, actor)
        self._require_active(upload)
        if upload.state == ArtifactUploadState.VERIFYING and upload.verified_sha256:
            verified = self.adapter._verified_existing(
                self.adapter.session_directory(upload.id) / "assembled.upload", upload
            )
            return upload, verified
        if upload.state != ArtifactUploadState.UPLOADING:
            raise ArtifactUploadError("artifact_upload_state_conflict")
        verified = self.adapter.assemble(upload)
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
        )
        return upload, verified

    def abort(self, session_id: str, actor: User) -> ArtifactUploadSession:
        upload = self.get(session_id, actor)
        if upload.state == ArtifactUploadState.ABORTED:
            return upload
        if upload.state in {ArtifactUploadState.COMPLETED, ArtifactUploadState.EXPIRED}:
            raise ArtifactUploadError("artifact_upload_state_conflict")
        self.adapter.abort_owned(upload)
        for part in self.parts(upload):
            self.session.delete(part)
        self.transition(upload, ArtifactUploadState.ABORTED)
        return upload

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
