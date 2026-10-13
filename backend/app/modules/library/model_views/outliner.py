"""Compose lightweight outliner leaves without loading their ORM aggregates."""

from app.schemas.outliner import (
    OutlinerCollectionMatch,
    OutlinerKind,
    OutlinerModel,
    OutlinerMultipart,
)


def entry_response(row, labels: dict[str, str]):
    fields = dict(
        id=row.id,
        name=row.name,
        collection_id=row.collection_id,
        collection=row.collection,
        collection_label=labels[row.collection] if row.collection is not None else None,
    )
    match OutlinerKind(row.kind):
        case OutlinerKind.MODEL:
            return OutlinerModel.model_validate(
                {
                    **fields,
                    "edit_version": row.edit_version,
                    "edit_epoch": row.edit_epoch,
                }
            )
        case OutlinerKind.MULTIPART:
            return OutlinerMultipart.model_validate(
                {
                    **fields,
                    "edit_version": row.edit_version,
                    "edit_epoch": row.edit_epoch,
                }
            )
        case OutlinerKind.COLLECTION:
            return OutlinerCollectionMatch.model_validate(fields)
