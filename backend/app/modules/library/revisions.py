"""SQLModel adapter for the shared Revision deletion operation.

Authorization and linked-source tombstones are checked here so non-HTTP callers
receive the same protection as the router. Stored bytes remain owned by trash.
"""

from __future__ import annotations

from types import TracebackType

from printstash_core.library import (
    RevisionDeletion,
    RevisionError,
    RevisionSnapshot,
    RevisionState,
    delete_revision,
)
from sqlalchemy import update
from sqlmodel import Session, col, select

from app.core.time import utcnow
from app.db.models import CollectionRole, File, Model, User
from app.db.projections import content_changed
from app.db.scopes import live
from app.modules.identity import rbac
from app.modules.library.trash import record_source_tombstone


class SQLRevisionUnitOfWork:
    def __init__(self, session: Session, actor: User) -> None:
        self.session = session
        self.actor = actor
        self.model: Model | None = None
        self.files: dict[int, File] = {}

    def __enter__(self) -> SQLRevisionUnitOfWork:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.session.rollback()

    def load(self, model_id: int) -> RevisionState:
        if self.actor.id is None or not self.actor.is_active:
            raise RevisionError("collection_permission_denied")
        connection = self.session.connection()
        if connection.dialect.name == "sqlite":
            # SQLite ignores FOR UPDATE. Acquire its writer reservation before
            # reading the recommendation, including on an already-open transaction.
            # This stages no logical change and is rolled back on authorization
            # failure; the real deletion and promotion still commit together.
            connection.execute(
                update(Model)
                .where(col(Model.id) == model_id, live(Model))
                .values(id=col(Model.id))
            )
        model = self.session.exec(
            select(Model)
            .where(Model.id == model_id, live(Model))
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if model is None:
            raise RevisionError("model_not_found")
        role = rbac.effective_collection_role(
            self.session, self.actor, model.collection_id
        )
        if not rbac.role_allows(role, CollectionRole.EDIT):
            raise RevisionError("collection_permission_denied")
        self.model = model
        files = self.session.exec(
            select(File)
            .where(File.model_id == model_id, live(File))
            .execution_options(populate_existing=True)
        ).all()
        snapshots = []
        for file in files:
            assert file.id is not None
            self.files[file.id] = file
            snapshots.append(
                RevisionSnapshot(
                    id=file.id,
                    version=file.version,
                    file_type=file.file_type.value,
                    is_recommended=file.is_recommended,
                )
            )
        return RevisionState(
            thumbnail_file_id=model.thumbnail_file_id,
            files=tuple(snapshots),
        )

    def apply(self, deletion: RevisionDeletion) -> None:
        assert self.model is not None
        file = self.files[deletion.file_id]
        now = utcnow()
        file.deleted_at = now
        file.deleted_by = self.actor.id
        file.is_recommended = False
        record_source_tombstone(self.session, file, "revision_trashed")
        self.session.add(file)
        # Clear the old partial unique-index entry before promotion.
        self.session.flush()
        if deletion.promote_file_id is not None:
            replacement = self.files[deletion.promote_file_id]
            replacement.is_recommended = True
            self.session.add(replacement)
        if deletion.clear_thumbnail:
            self.model.thumbnail_file_id = None
            self.model.thumbnail_path = None
        self.model.updated_at = now
        self.session.add(self.model)
        content_changed(self.session, "model", [self.model.id])

    def commit(self) -> None:
        self.session.commit()


def remove_revision(
    session: Session, actor: User, model_id: int, file_id: int
) -> RevisionDeletion:
    return delete_revision(model_id, file_id, uow=SQLRevisionUnitOfWork(session, actor))
