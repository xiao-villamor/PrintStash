"""Caption ownership, durable tombstones and synchronous search invalidation.

This owner never writes a library description. Inference runs after the caller's
transaction; version tokens fence both concurrent human actions and workers.
"""

import secrets

from printstash_core.search.passages import SearchSubject, SubjectType
from sqlalchemy import update
from sqlmodel import Session, select

from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.models import (
    Collection,
    CollectionRole,
    Document,
    File,
    InferenceEndpoint,
    Model,
    MultipartModel,
    SubjectCaption,
    User,
)
from app.db.transactions import begin_write
from app.modules.administration import audit
from app.modules.identity.rbac import effective_collection_role, require_collection_role
from app.modules.inference.configuration import load as load_endpoint
from app.modules.search.access import visible_subjects
from app.modules.search.caption_source import RECIPE, source
from app.modules.search.configuration import settings
from app.schemas.captions import CaptionPatch, CaptionRead

OWNERS = {
    SubjectType.MODEL: Model,
    SubjectType.COLLECTION: Collection,
    SubjectType.MULTIPART_MODEL: MultipartModel,
    SubjectType.DOCUMENT: Document,
}


def lookup(session: Session, subject: SearchSubject, *, lock=False):
    statement = select(SubjectCaption).where(
        SubjectCaption.subject_type == subject.subject_type.value,
        SubjectCaption.subject_id == subject.subject_id,
    )
    if lock:
        statement = statement.with_for_update()
    return session.exec(statement.execution_options(populate_existing=True)).first()


def require(session: Session, actor: User, subject: SearchSubject, *, edit=False):
    visible = visible_subjects(session, actor)
    if (
        session.exec(
            select(visible.c.id).where(
                visible.c.kind == subject.subject_type.value,
                visible.c.id == subject.subject_id,
            )
        ).first()
        is None
    ):
        raise OperationError("caption_subject_not_found", kind=ErrorKind.NOT_FOUND)
    owner = session.exec(
        select(OWNERS[subject.subject_type])
        .where(OWNERS[subject.subject_type].id == subject.subject_id)
        .with_for_update()
    ).one()
    collection_id = (
        owner.id
        if subject.subject_type == SubjectType.COLLECTION
        else owner.collection_id
    )
    if edit:
        require_collection_role(session, actor, collection_id, CollectionRole.EDIT)
    return owner, collection_id


def endpoint(session: Session):
    config = settings(session)
    if not config.enabled or not config.captions_enabled:
        return None, "caption_disabled"
    row = (
        session.get(InferenceEndpoint, config.chat_endpoint_id)
        if config.chat_endpoint_id
        else None
    )
    if (
        not row
        or row.kind != "chat"
        or not row.supports_images
        or not config.send_rendered_images
    ):
        return None, "inference_caption_consent_required"
    return row, None


def new(subject: SearchSubject):
    return SubjectCaption(
        subject_type=subject.subject_type.value,
        subject_id=subject.subject_id,
        version_token=secrets.token_hex(16),
        **{f"{subject.subject_type.value}_id": subject.subject_id},
    )


def queue(
    session: Session,
    row: SubjectCaption,
    file: File,
    provider: InferenceEndpoint,
    *,
    actor_id: int | None,
):
    row.state, row.phase, row.text = "generated", "pending", ""
    row.recipe = RECIPE
    row.version_token = secrets.token_hex(16)
    row.input_hash, row.source_file_id = file.sha256, file.id
    row.endpoint_id, row.provider_identity = provider.id, provider.config_hash
    config = load_endpoint(provider)
    row.model, row.model_revision = config.model, config.revision
    row.actor_id, row.edited_by = actor_id, None
    row.attempts, row.error_code, row.retry_after = 0, None, None
    row.job_id = None
    row.lease_token, row.lease_expires_at = None, None
    row.updated_at = utcnow()
    session.add(row)
    session.flush()


def read(session: Session, actor: User, subject: SearchSubject) -> CaptionRead:
    _, collection_id = require(session, actor, subject)
    role = effective_collection_role(session, actor, collection_id)
    can_edit = actor.is_superuser or role in (CollectionRole.EDIT, CollectionRole.ADMIN)
    provider, reason = endpoint(session)
    if provider and source(session, subject) is None:
        reason = "caption_preview_unavailable"
    row = lookup(session, subject)
    values = (
        {
            key: getattr(row, key)
            for key in (
                "state",
                "phase",
                "text",
                "version_token",
                "model",
                "model_revision",
                "recipe",
                "edited_by",
                "updated_at",
                "error_code",
            )
        }
        if row
        else {}
    )
    return CaptionRead(
        can_edit=can_edit,
        can_generate=reason is None,
        unavailable_reason=reason,
        **values,
    )


def patch(
    session: Session, actor: User, subject: SearchSubject, value: CaptionPatch
) -> CaptionRead:
    from app.modules.search.passages import sync_subject

    begin_write(session)
    require(session, actor, subject, edit=True)
    row = lookup(session, subject, lock=True)
    if value.version_token is not None and (
        row is None or row.version_token != value.version_token
    ):
        raise OperationError("caption_changed", kind=ErrorKind.CONFLICT)
    if row is not None:
        claimed = session.exec(
            update(SubjectCaption)
            .where(
                SubjectCaption.id == row.id,
                SubjectCaption.version_token == row.version_token,
            )
            .values(updated_at=utcnow())
        )
        if claimed.rowcount != 1:
            raise OperationError("caption_changed", kind=ErrorKind.CONFLICT)
    row = row or new(subject)
    old_version = row.version_token
    if value.action in ("generate", "reset"):
        if value.action == "generate" and row.state in ("edited", "dismissed"):
            raise OperationError("caption_reset_required", kind=ErrorKind.CONFLICT)
        provider, reason = endpoint(session)
        file = source(session, subject)
        if reason or file is None:
            raise OperationError(
                reason or "caption_preview_unavailable", kind=ErrorKind.CONFLICT
            )
        if (
            value.action == "generate"
            and row.id is not None
            and row.phase in ("pending", "running", "ready")
            and row.input_hash == file.sha256
            and row.source_file_id == file.id
            and row.provider_identity == provider.config_hash
            and row.recipe == RECIPE
        ):
            session.commit()
            return read(session, actor, subject)
        queue(session, row, file, provider, actor_id=actor.id)
    else:
        row.state = "edited" if value.action == "edit" else "dismissed"
        row.text = value.text.strip() if value.action == "edit" else ""
        row.phase, row.error_code = "ready", None
        row.edited_by, row.updated_at = actor.id, utcnow()
        row.lease_token, row.lease_expires_at = None, None
        row.version_token = secrets.token_hex(16)
        # Dismissal retains only provenance/tombstone metadata, never generated text.
        session.add(row)
        session.flush()
    sync_subject(session, subject)
    audit.record(
        session,
        action="subject_caption_" + value.action,
        resource_type=subject.subject_type.value,
        resource_id=subject.subject_id,
        actor_id=actor.id,
        diff={
            "state": row.state,
            "previous_version": old_version,
            "version": row.version_token,
        },
    )
    return read(session, actor, subject)
