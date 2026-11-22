"""Read the meshless-archive publication using the Artifact's current bytes."""

import json

from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Session, col

from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeState,
    File,
    FileType,
)
from app.db.scopes import live
from app.modules.media.sliced_archive import ArchiveImage, SlicedGeometry


def meshless_archive_rows(session: Session, model_ids: list[int]):
    """Select only publications that describe the Artifact's current bytes."""
    output = col(ArtifactDerivative.output_json)
    if session.get_bind().dialect.name == "postgresql":
        document = cast(output, JSONB)
        geometry = document["geometry"].astext
        source_sha256 = document["source_sha256"].astext
    else:
        geometry = func.json_extract(output, "$.geometry")
        source_sha256 = func.json_extract(output, "$.source_sha256")
    return (
        select(
            col(File.id).label("file_id"),
            col(File.model_id).label("model_id"),
            col(File.sha256).label("sha256"),
            output.label("output"),
            col(File.version).label("file_version"),
            col(ArtifactDerivative.recipe_version).label("recipe_version"),
        )
        .join(ArtifactDerivative, col(ArtifactDerivative.file_id) == col(File.id))
        .where(
            col(File.model_id).in_(model_ids),
            col(File.file_type).in_((FileType.THREE_MF, FileType.GCODE)),
            live(File),
            col(ArtifactDerivative.kind) == DerivativeKind.METADATA,
            col(ArtifactDerivative.state) == DerivativeState.READY,
            geometry == SlicedGeometry.GCODE_ONLY.value,
            source_sha256 == col(File.sha256),
        )
    )


def meshless_archives(session: Session, model_ids: list[int]):
    candidates = meshless_archive_rows(session, model_ids).subquery()
    rows = session.execute(
        select(
            candidates.c.file_id,
            candidates.c.model_id,
            candidates.c.sha256,
            candidates.c.output,
        ).order_by(candidates.c.file_version.desc(), candidates.c.recipe_version.desc())
    )
    result: dict[int, tuple[int, str, tuple[ArchiveImage, ...]]] = {}
    for file_id, model_id, sha256, output in rows:
        data = json.loads(output)
        if (
            file_id is not None
            and file_id not in result
            and data.get("geometry") == SlicedGeometry.GCODE_ONLY.value
            and data.get("source_sha256") == sha256
        ):
            images = tuple(ArchiveImage(**image) for image in data["archive_images"])
            result[file_id] = (model_id, sha256, images)
    return result
