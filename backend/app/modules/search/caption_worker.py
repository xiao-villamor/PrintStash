"""One bounded caption lease at a time; no originals, prompts or partial text persist."""

import secrets
from datetime import timedelta
from io import BytesIO

from PIL import Image
from printstash_core.inference import EmbeddingError, InferenceContext
from printstash_core.inference.chat import ChatInput
from printstash_core.search.passages import SearchSubject, SubjectType
from printstash_core.search.visual_inputs import VisualRecipe
from sqlalchemy import or_, update
from sqlmodel import select

from app.core.errors import OperationError
from app.core.time import utcnow
from app.db.models import BackgroundJob, File, Model, SubjectCaption, SystemConfig, User
from app.modules.inference.configuration import chat_provider
from app.modules.search import caption_source, captions
from app.modules.search.passages import sync_subject
from app.modules.search.visual_index import render
from app.modules.search.visual_sources import eligible
from app.runtime.jobs import registry


def render_image(sessions, file, context):
    # Uses the existing bounded media recipe and containment, without requiring
    # an embedding model. The all-zero encoder identity is never stored as a Space.
    views = render(
        sessions,
        file,
        VisualRecipe("0" * 64, caption_source.RENDER_SIZE, "thumbnail"),
        context,
    )
    frame = views.thumbnail
    if frame is None:
        raise EmbeddingError("caption_preview_unavailable")
    image = Image.frombytes("RGB", (frame.width, frame.height), frame.rgb)
    encoded = BytesIO()
    image.save(encoded, format="JPEG", quality=caption_source.JPEG_QUALITY)
    return encoded.getvalue()


def sweep(session):
    provider, _ = captions.endpoint(session)
    if provider is None:
        return 0
    files = session.exec(
        select(File)
        .outerjoin(SubjectCaption, SubjectCaption.model_id == File.model_id)
        .where(
            File.id.in_(eligible(session)),
            or_(
                SubjectCaption.id.is_(None),
                (SubjectCaption.state == "generated")
                & or_(
                    SubjectCaption.input_hash != File.sha256,
                    SubjectCaption.source_file_id != File.id,
                    SubjectCaption.provider_identity != provider.config_hash,
                    SubjectCaption.recipe != caption_source.RECIPE,
                ),
            ),
        )
        .order_by(File.id)
        .limit(8)
    ).all()
    config = session.get(SystemConfig, 1)
    for file in files:
        subject = SearchSubject(SubjectType.MODEL, file.model_id)
        session.exec(
            select(Model).where(Model.id == file.model_id).with_for_update()
        ).one()
        row = captions.lookup(session, subject, lock=True)
        if row and row.state != "generated":
            continue
        row = row or captions.new(subject)
        captions.queue(
            session,
            row,
            file,
            provider,
            actor_id=config.ai_search_configured_by if config else None,
        )
        sync_subject(session, subject)
    return len(files)


class CaptionProcessor:
    def __init__(
        self, sessions, *, provider_factory=chat_provider, image_renderer=render_image
    ):
        self.sessions, self.provider_factory, self.image_renderer = (
            sessions,
            provider_factory,
            image_renderer,
        )

    def work_one(self):
        with self.sessions.scoped_session() as session:
            expired = session.exec(
                select(SubjectCaption)
                .where(
                    SubjectCaption.phase == "running",
                    SubjectCaption.attempts >= 3,
                    SubjectCaption.lease_expires_at < utcnow(),
                )
                .order_by(SubjectCaption.id)
                .limit(8)
                .with_for_update(skip_locked=True)
            ).all()
            exhausted_jobs = []
            for item in expired:
                item.phase, item.error_code = "failed", "caption_attempts_exhausted"
                item.lease_token, item.lease_expires_at = None, None
                if item.job_id:
                    exhausted_jobs.append(item.job_id)
                session.add(item)
            if expired:
                session.commit()
                for job in exhausted_jobs:
                    registry.update(
                        job, state="failed", error="caption_attempts_exhausted"
                    )
            provider, _ = captions.endpoint(session)
            if provider is None:
                return False
            sweep(session)
            now = utcnow()
            row = session.exec(
                select(SubjectCaption)
                .where(
                    SubjectCaption.state == "generated",
                    SubjectCaption.attempts < 3,
                    or_(
                        SubjectCaption.phase == "pending",
                        (SubjectCaption.phase == "running")
                        & (SubjectCaption.lease_expires_at < now),
                    ),
                    or_(
                        SubjectCaption.retry_after.is_(None),
                        SubjectCaption.retry_after <= now,
                    ),
                )
                .order_by(SubjectCaption.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if row is None:
                session.commit()
                return False
            token = secrets.token_hex(16)
            claimed = session.exec(
                update(SubjectCaption)
                .execution_options(synchronize_session="fetch")
                .where(
                    SubjectCaption.id == row.id,
                    SubjectCaption.version_token == row.version_token,
                    or_(
                        SubjectCaption.phase == "pending",
                        SubjectCaption.lease_expires_at < now,
                    ),
                )
                .values(
                    phase="running",
                    lease_token=token,
                    lease_expires_at=now + timedelta(seconds=180),
                    attempts=SubjectCaption.attempts + 1,
                )
            )
            if claimed.rowcount != 1:
                session.rollback()
                return False
            previous_job = (
                session.get(BackgroundJob, row.job_id) if row.job_id else None
            )
            if previous_job is None or previous_job.state in ("completed", "failed"):
                row.job_id = registry.create(
                    row.actor_id, kind="ai_caption", session=session
                )
            session.add(row)
            session.flush()
            id, version, input_hash, job_id = (
                row.id,
                row.version_token,
                row.input_hash,
                row.job_id,
            )
            subject = SearchSubject(SubjectType(row.subject_type), row.subject_id)
            file = caption_source.source(session, subject)
            if (
                file is None
                or file.id != row.source_file_id
                or file.sha256 != input_hash
            ):
                row.phase, row.error_code = "failed", "caption_source_changed"
                row.lease_token, row.lease_expires_at = None, None
                session.add(row)
                session.commit()
                registry.update(job_id, state="failed", error="caption_source_changed")
                return True
            endpoint_id = row.endpoint_id
            session.expunge(file)
            session.commit()
        registry.update(job_id, state="running")
        error = None
        text = None
        try:
            context = InferenceContext.bounded(120, priority="background")
            with self.sessions.scoped_session() as session:
                provider_instance = self.provider_factory(session, endpoint_id)
            image = self.image_renderer(self.sessions, file, context)
            # Recheck consent and human changes after potentially slow rendering,
            # immediately before any image leaves the instance.
            with self.sessions.scoped_session() as session:
                if not self._current(session, subject, id, version, input_hash, token):
                    self._cancel(session, id, version, token)
                    registry.update(
                        job_id, state="completed", result={"cancelled": True}
                    )
                    return True
            result = provider_instance.complete(
                ChatInput(
                    caption_source.INSTRUCTION,
                    "Describe this object.",
                    caption_source.CAPTION_SCHEMA,
                    image_jpegs=(image,),
                    max_output_tokens=512,
                ),
                context=context,
            )
            value = result.value
            if (
                not isinstance(value, dict)
                or set(value) != {"caption"}
                or not isinstance(value["caption"], str)
                or len(value["caption"]) > 2048
                or not value["caption"].strip()
            ):
                raise EmbeddingError("caption_output_invalid")
            text = value["caption"].strip()
            context.remaining()
        except EmbeddingError as exc:
            error = exc.code
        except Exception:
            error = "caption_generation_failed"
        with self.sessions.scoped_session() as session:
            row = self._current(session, subject, id, version, input_hash, token)
            if row is None:
                self._cancel(session, id, version, token)
                registry.update(job_id, state="completed", result={"cancelled": True})
                return True
            # CAS covers SQLite and the last race after a visibility/source check.
            values = {
                "phase": "ready"
                if error is None
                else "failed"
                if row.attempts >= 3
                else "pending",
                "text": text if error is None else "",
                "error_code": error,
                "lease_token": None,
                "lease_expires_at": None,
                "updated_at": utcnow(),
                "retry_after": utcnow() + timedelta(seconds=30 * row.attempts)
                if error
                else None,
            }
            changed = session.exec(
                update(SubjectCaption)
                .execution_options(synchronize_session="fetch")
                .where(
                    SubjectCaption.id == id,
                    SubjectCaption.version_token == version,
                    SubjectCaption.lease_token == token,
                    SubjectCaption.state == "generated",
                    select(File.id)
                    .where(
                        File.id == row.source_file_id,
                        File.sha256 == input_hash,
                        File.id.in_(eligible(session)),
                    )
                    .exists(),
                )
                .values(**values)
            )
            if changed.rowcount != 1:
                session.rollback()
                registry.update(job_id, state="completed", result={"cancelled": True})
                return True
            sync_subject(session, subject)
            exhausted = row.attempts >= 3
            session.commit()
        if error is None or exhausted:
            registry.update(
                job_id, state="failed" if error else "completed", error=error
            )
        return True

    def _cancel(self, session, id, version, token):
        session.exec(
            update(SubjectCaption)
            .where(
                SubjectCaption.id == id,
                SubjectCaption.version_token == version,
                SubjectCaption.lease_token == token,
                SubjectCaption.state == "generated",
            )
            .values(
                phase="failed",
                error_code="caption_cancelled",
                lease_token=None,
                lease_expires_at=None,
            )
        )
        session.commit()

    def _current(self, session, subject, id, version, input_hash, token):
        owner = session.exec(
            select(captions.OWNERS[subject.subject_type])
            .where(captions.OWNERS[subject.subject_type].id == subject.subject_id)
            .with_for_update()
        ).first()
        row = captions.lookup(session, subject, lock=True)
        provider, _ = captions.endpoint(session)
        file = caption_source.source(session, subject)
        if (
            owner is None
            or row is None
            or row.id != id
            or row.state != "generated"
            or row.version_token != version
            or row.lease_token != token
            or row.input_hash != input_hash
            or provider is None
            or provider.config_hash != row.provider_identity
            or row.recipe != caption_source.RECIPE
            or file is None
            or file.id != row.source_file_id
            or file.sha256 != input_hash
        ):
            return None
        if row.actor_id is not None:
            actor = session.get(User, row.actor_id)
            if actor is None or not actor.is_active:
                return None
            try:
                captions.require(session, actor, subject, edit=True)
            except OperationError:
                return None
        return row
