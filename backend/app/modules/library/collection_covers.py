from typing import Any

from sqlmodel import Session, col, select

from app.db.models import Collection, Model, User
from app.db.scopes import live
from app.modules.library.model_views.access import accessible_live_model_ids_stmt
from app.schemas.collection_covers import (
    AutomaticCover,
    CollectionCover,
    ImageCover,
    ModelsCover,
    custom_cover_adapter,
)


def image_url(collection_id: int, filename: str) -> str:
    return f"/api/v1/collections/{collection_id}/images/{filename}"


def image_filename(collection: Collection) -> str | None:
    if collection.cover_selection is None:
        return None
    selection = custom_cover_adapter.validate_python(collection.cover_selection)
    return selection.filename if isinstance(selection, ImageCover) else None


def eligible_model_ids(
    session: Session, user: User, path: str, requested: list[int]
) -> list[int]:
    eligible = set(
        session.exec(
            select(col(Model.id))
            .join(Collection, col(Model.collection_id) == col(Collection.id))
            .where(
                col(Model.id).in_(requested),
                col(Model.id).in_(accessible_live_model_ids_stmt(session, user)),
                live(Collection),
                (col(Collection.path) == path)
                | col(Collection.path).startswith(f"{path}/", autoescape=True),
            )
        ).all()
    )
    return [model_id for model_id in requested if model_id in eligible]


def read_cover(
    session: Session, user: User, path: str, saved: dict[str, Any] | None
) -> CollectionCover:
    if saved is not None:
        selection = custom_cover_adapter.validate_python(saved)
        if not isinstance(selection, ModelsCover):
            return selection
        model_ids = eligible_model_ids(session, user, path, selection.model_ids)
        if model_ids:
            return ModelsCover(kind="models", model_ids=model_ids)
    return AutomaticCover(kind="automatic")
