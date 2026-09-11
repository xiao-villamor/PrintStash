"""Authorized library mutations callable from HTTP or a worker.

Each operation owns its commit; taxonomy helpers only stage rows. Query
projections remain with the caller, so a mutation does not require HTTP output.
"""

from __future__ import annotations

from typing import List, Optional

from sqlmodel import Session, delete, select

import app.modules.library.revision_labels as models_revision_labels
from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.models import (
    Collection,
    CollectionRole,
    File,
    FileTagLink,
    FileType,
    Model,
    ModelStar,
    ModelTagLink,
    Tag,
    User,
)
from app.db.scopes import live
from app.db.transactions import rollback_on_failure
from app.modules.identity import rbac
from app.modules.library import (
    taxonomy,
)
from app.modules.library.trash import (
    soft_delete_models,
)
from app.schemas.models import (
    FileRevisionUpdate,
    ModelBatchDelete,
    ModelBatchFailure,
    ModelBatchMove,
    ModelBatchResult,
    ModelBatchTags,
    ModelUpdate,
    RevisionBatchLabels,
    RevisionBatchResult,
    TagSetUpdate,
)
from app.schemas.saved_views import ModelStarRead


def _live_model(session: Session, model_id: int) -> Model:
    """Like ``get_or_404`` but also rejects soft-deleted rows."""
    m = session.exec(select(Model).where(Model.id == model_id, live(Model))).first()
    if m is None:
        raise OperationError("model_not_found", kind=ErrorKind.NOT_FOUND)
    return m


def _require_model_role(
    session: Session,
    user: User,
    model_id: int,
    minimum: CollectionRole,
) -> Model:
    model = _live_model(session, model_id)
    if model.collection_id is None and not user.is_superuser:
        raise OperationError("root_collection_admin_required", kind=ErrorKind.FORBIDDEN)
    _require_collection_role(session, user, model.collection_id, minimum)
    return model


def _collection_path_for(raw_path: str) -> str:
    segments = [taxonomy.slugify(s.strip()) for s in raw_path.split("/") if s.strip()]
    return "/".join(segments)


def star_model(
    model_id: int, current_user: User, session: Session, *, commit: bool = True
) -> ModelStarRead:
    with rollback_on_failure(session):
        if not current_user.is_active:
            raise OperationError(
                "collection_permission_denied", kind=ErrorKind.FORBIDDEN
            )
        _require_model_role(session, current_user, model_id, CollectionRole.VIEW)
        existing = session.exec(
            select(ModelStar).where(
                ModelStar.user_id == current_user.id, ModelStar.model_id == model_id
            )
        ).first()
        if existing is None:
            session.add(ModelStar(user_id=current_user.id, model_id=model_id))
            if commit:
                session.commit()
            else:
                session.flush()
        return ModelStarRead(model_id=model_id, starred=True)


def unstar_model(model_id: int, current_user: User, session: Session) -> ModelStarRead:
    with rollback_on_failure(session):
        if not current_user.is_active:
            raise OperationError(
                "collection_permission_denied", kind=ErrorKind.FORBIDDEN
            )
        _require_model_role(session, current_user, model_id, CollectionRole.VIEW)
        session.exec(
            delete(ModelStar).where(
                ModelStar.user_id == current_user.id, ModelStar.model_id == model_id
            )
        )
        session.commit()
        return ModelStarRead(model_id=model_id, starred=False)


def _dedupe_ids(ids: List[int]) -> List[int]:
    seen: set[int] = set()
    out: List[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _partition_editable_models(
    session: Session, user: User, ids: List[int]
) -> tuple[List[Model], List[ModelBatchFailure]]:
    """Single-pass RBAC for batch ops: split ``ids`` into editable models and
    per-id failures, preserving input order.

    Replaces a per-model ``_require_model_role`` loop, which re-ran the same
    "all of this user's grants" query once per model (an N+1 that scaled with
    the selection). Here the models are fetched in one query and the editable
    collection set is computed once. Failure reasons match the single-model
    endpoint so the client sees the same ``reason`` strings.
    """
    rows = session.exec(
        select(Model).where(Model.id.in_(ids), live(Model))  # type: ignore[union-attr]
    ).all()
    by_id = {m.id: m for m in rows}
    # Superuser short-circuits inside accessible_collection_ids (returns every
    # collection), so a single call covers both roles.
    editable_ids = rbac.accessible_collection_ids(session, user, CollectionRole.EDIT)
    editable: List[Model] = []
    failed: List[ModelBatchFailure] = []
    for mid in ids:
        m = by_id.get(mid)
        if m is None:
            failed.append(ModelBatchFailure(model_id=mid, reason="model_not_found"))
        elif m.collection_id is None:
            if user.is_superuser:
                editable.append(m)
            else:
                failed.append(
                    ModelBatchFailure(
                        model_id=mid, reason="root_collection_admin_required"
                    )
                )
        elif m.collection_id in editable_ids:
            editable.append(m)
        else:
            failed.append(
                ModelBatchFailure(model_id=mid, reason="collection_permission_denied")
            )
    return editable, failed


def _require_all_editable_models(
    session: Session, user: User, ids: List[int]
) -> List[Model]:
    editable, failed = _partition_editable_models(session, user, _dedupe_ids(ids))
    if failed:
        error_kind = (
            ErrorKind.NOT_FOUND
            if failed[0].reason == "model_not_found"
            else ErrorKind.FORBIDDEN
        )
        raise OperationError(failed[0].reason, kind=error_kind)
    return editable


def batch_move_models(
    payload: ModelBatchMove,
    current_user: User,
    session: Session,
    *,
    commit: bool = True,
) -> ModelBatchResult:
    with rollback_on_failure(session):
        if not current_user.is_active:
            raise OperationError(
                "collection_permission_denied", kind=ErrorKind.FORBIDDEN
            )
        dest_is_root = payload.collection.strip() == ""
        if dest_is_root and not current_user.is_superuser:
            raise OperationError(
                "root_collection_admin_required", kind=ErrorKind.FORBIDDEN
            )

        existing_dest: Optional[Collection] = None
        if not dest_is_root:
            collection_path = _collection_path_for(payload.collection)
            existing_dest = session.exec(
                select(Collection).where(
                    Collection.path == collection_path, live(Collection)
                )
            ).first()
            if existing_dest is None and not current_user.is_superuser:
                raise OperationError(
                    "collection_permission_denied", kind=ErrorKind.FORBIDDEN
                )
            if existing_dest is not None:
                _require_collection_role(
                    session, current_user, existing_dest.id, CollectionRole.EDIT
                )

        editable = _require_all_editable_models(
            session, current_user, payload.model_ids
        )

        if dest_is_root:
            dest_id: Optional[int] = None
        elif existing_dest is not None:
            dest_id = existing_dest.id
        else:
            cat = taxonomy.resolve_or_create_collection_in_transaction(
                session, payload.collection
            )
            _require_collection_role(session, current_user, cat.id, CollectionRole.EDIT)
            dest_id = cat.id

        succeeded: List[int] = []
        for m in editable:
            m.collection_id = dest_id
            m.updated_at = utcnow()
            session.add(m)
            succeeded.append(m.id)  # type: ignore[arg-type]

        if commit:
            session.commit()
        else:
            session.flush()
        return ModelBatchResult(
            succeeded_ids=succeeded,
            failed=[],
            succeeded_count=len(succeeded),
            failed_count=0,
        )


def batch_tag_models(
    payload: ModelBatchTags,
    current_user: User,
    session: Session,
    *,
    commit: bool = True,
) -> ModelBatchResult:
    with rollback_on_failure(session):
        if not current_user.is_active:
            raise OperationError(
                "collection_permission_denied", kind=ErrorKind.FORBIDDEN
            )
        editable = _require_all_editable_models(
            session, current_user, payload.model_ids
        )

        add_tags = (
            taxonomy.resolve_or_create_tags_in_transaction(session, payload.add)
            if payload.add
            else []
        )
        # Removal only targets tags that already exist; never create on remove.
        remove_tag_ids: List[int] = []
        for raw in payload.remove:
            # Guard the *input*, not the slug. `slugify` falls back to "model" for
            # anything it cannot make a slug out of, so slugging first turned
            # `remove: ["   "]` into "remove the tag named model" — and the guard
            # underneath it could never fire.
            name = raw.strip()
            if not name:
                continue
            slug = taxonomy.slugify(name)
            tag = session.exec(select(Tag).where(Tag.slug == slug, live(Tag))).first()
            if tag is not None and tag.id is not None:
                remove_tag_ids.append(tag.id)

        succeeded: List[int] = []
        for m in editable:
            model_id = m.id
            if add_tags:
                existing = set(
                    session.exec(
                        select(ModelTagLink.tag_id).where(
                            ModelTagLink.model_id == model_id
                        )
                    ).all()
                )
                for t in add_tags:
                    if t.id not in existing:
                        session.add(ModelTagLink(model_id=model_id, tag_id=t.id))
            if remove_tag_ids:
                session.exec(
                    delete(ModelTagLink).where(  # type: ignore[call-overload]
                        ModelTagLink.model_id == model_id,
                        ModelTagLink.tag_id.in_(remove_tag_ids),  # type: ignore[attr-defined]
                    )
                )
            m.updated_at = utcnow()
            session.add(m)
            succeeded.append(model_id)

        if commit:
            session.commit()
        else:
            session.flush()
        return ModelBatchResult(
            succeeded_ids=succeeded,
            failed=[],
            succeeded_count=len(succeeded),
            failed_count=0,
        )


def batch_set_revision_labels(
    payload: RevisionBatchLabels, current_user: User, session: Session
) -> RevisionBatchResult:
    with rollback_on_failure(session):
        if not current_user.is_active:
            raise OperationError(
                "collection_permission_denied", kind=ErrorKind.FORBIDDEN
            )
        file_ids = _dedupe_ids(payload.file_ids)
        rows = session.exec(
            select(File).where(File.id.in_(file_ids), live(File))  # type: ignore[union-attr]
        ).all()
        by_id = {row.id: row for row in rows}
        ordered: List[File] = []
        for file_id in file_ids:
            row = by_id.get(file_id)
            if row is None:
                raise OperationError("file_not_found", kind=ErrorKind.NOT_FOUND)
            if row.file_type != FileType.GCODE:
                raise OperationError("revision_not_supported", kind=ErrorKind.INVALID)
            ordered.append(row)

        # Called for the authorization it enforces: it raises 404 for a model that is
        # not there and 403 for one this user may not edit, on the first failure. The
        # returned rows are not needed here, and a second "did every id come back?"
        # check after it could never fire.
        _require_all_editable_models(
            session, current_user, [row.model_id for row in ordered]
        )

        try:
            models_revision_labels.set_revision_labels(
                session, ordered, payload.revision_label
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        return RevisionBatchResult(
            succeeded_ids=file_ids, succeeded_count=len(file_ids)
        )


def batch_delete_models(
    payload: ModelBatchDelete, current_user: User, session: Session
) -> ModelBatchResult:
    with rollback_on_failure(session):
        if not current_user.is_active:
            raise OperationError(
                "collection_permission_denied", kind=ErrorKind.FORBIDDEN
            )
        editable = _require_all_editable_models(
            session, current_user, payload.model_ids
        )
        soft_delete_models(session, editable)
        session.commit()
        return ModelBatchResult(
            succeeded_ids=[m.id for m in editable],  # type: ignore[misc]
            failed=[],
            succeeded_count=len(editable),
            failed_count=0,
        )


def update_model(
    model_id: int, payload: ModelUpdate, current_user: User, session: Session
) -> None:
    with rollback_on_failure(session):
        if not current_user.is_active:
            raise OperationError(
                "collection_permission_denied", kind=ErrorKind.FORBIDDEN
            )
        m = _require_model_role(session, current_user, model_id, CollectionRole.EDIT)

        if payload.name is not None:
            m.name = payload.name.strip() or m.name
        if payload.description is not None:
            m.description = payload.description
        if "source_url" in payload.model_fields_set:
            m.source_url = payload.source_url

        if payload.collection is not None:
            if payload.collection.strip() == "":
                if not current_user.is_superuser:
                    raise OperationError(
                        "root_collection_admin_required", kind=ErrorKind.FORBIDDEN
                    )
                m.collection_id = None
            else:
                collection_path = _collection_path_for(payload.collection)
                cat = session.exec(
                    select(Collection).where(
                        Collection.path == collection_path, live(Collection)
                    )
                ).first()
                if cat is None:
                    if not current_user.is_superuser:
                        raise OperationError(
                            "collection_permission_denied", kind=ErrorKind.FORBIDDEN
                        )
                    cat = taxonomy.resolve_or_create_collection_in_transaction(
                        session, payload.collection
                    )
                if cat is not None:
                    _require_collection_role(
                        session,
                        current_user,
                        cat.id,
                        CollectionRole.EDIT,
                    )
                    m.collection_id = cat.id

        if payload.tags is not None:
            session.exec(delete(ModelTagLink).where(ModelTagLink.model_id == model_id))  # type: ignore[call-overload]
            if payload.tags:
                new_tags = taxonomy.resolve_or_create_tags_in_transaction(
                    session, payload.tags
                )
                for t in new_tags:
                    session.add(ModelTagLink(model_id=model_id, tag_id=t.id))

        m.updated_at = utcnow()
        session.add(m)
        session.commit()
        return None


def update_file_revision(
    model_id: int,
    file_id: int,
    payload: FileRevisionUpdate,
    current_user: User,
    session: Session,
) -> None:
    with rollback_on_failure(session):
        if not current_user.is_active:
            raise OperationError(
                "collection_permission_denied", kind=ErrorKind.FORBIDDEN
            )
        m = _require_model_role(session, current_user, model_id, CollectionRole.EDIT)
        file_row = session.get(File, file_id)
        if (
            file_row is None
            or file_row.model_id != model_id
            or file_row.deleted_at is not None
        ):
            raise OperationError("file_not_found", kind=ErrorKind.NOT_FOUND)
        if file_row.file_type != FileType.GCODE:
            raise OperationError("revision_not_supported", kind=ErrorKind.INVALID)

        fields = payload.model_fields_set
        if "revision_label" in fields:
            label = payload.revision_label
            file_row.revision_label = label.strip() if label and label.strip() else None
        if "revision_status" in fields:
            file_row.revision_status = payload.revision_status
        if "revision_notes" in fields:
            notes = payload.revision_notes
            file_row.revision_notes = notes.strip() if notes and notes.strip() else None
        if "is_recommended" in fields:
            make_recommended = bool(payload.is_recommended)
            if make_recommended:
                other_gcode = session.exec(
                    select(File).where(
                        File.model_id == model_id,
                        File.id != file_id,
                        File.file_type == FileType.GCODE,
                        live(File),
                    )
                ).all()
                for other in other_gcode:
                    other.is_recommended = False
                    session.add(other)
                if other_gcode:
                    session.flush()
            file_row.is_recommended = make_recommended

        m.updated_at = utcnow()
        session.add(file_row)
        session.add(m)
        session.commit()
        return None


def replace_file_tags(
    model_id: int,
    file_id: int,
    payload: TagSetUpdate,
    current_user: User,
    session: Session,
) -> None:
    with rollback_on_failure(session):
        if not current_user.is_active:
            raise OperationError(
                "collection_permission_denied", kind=ErrorKind.FORBIDDEN
            )
        model = _require_model_role(
            session, current_user, model_id, CollectionRole.EDIT
        )
        file_row = session.get(File, file_id)
        if (
            file_row is None
            or file_row.model_id != model_id
            or file_row.deleted_at is not None
        ):
            raise OperationError("file_not_found", kind=ErrorKind.NOT_FOUND)

        session.exec(delete(FileTagLink).where(FileTagLink.file_id == file_id))
        tags = taxonomy.resolve_or_create_tags_in_transaction(session, payload.tags)
        for tag in tags:
            session.add(FileTagLink(file_id=file_id, tag_id=tag.id))
        model.updated_at = utcnow()
        session.add(model)
        session.commit()
        return None


def _require_collection_role(session, user, collection_id, minimum):
    role = rbac.effective_collection_role(session, user, collection_id)
    if not user.is_active or not rbac.role_allows(role, minimum):
        raise OperationError("collection_permission_denied", kind=ErrorKind.FORBIDDEN)
    return role
