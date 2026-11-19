"""Read the meshless-archive publication using the Artifact's current bytes."""

import json

from sqlmodel import Session, col, select

from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeState,
    File,
    FileType,
)
from app.db.scopes import live
from app.modules.media.sliced_archive import ArchiveImage, SlicedGeometry


def meshless_archives(session: Session, model_ids: list[int]):
    rows = session.exec(
        select(File.id, File.model_id, File.sha256, ArtifactDerivative.output_json)
        .join(ArtifactDerivative, col(ArtifactDerivative.file_id) == col(File.id))
        .where(
            col(File.model_id).in_(model_ids),
            col(File.file_type).in_((FileType.THREE_MF, FileType.GCODE)),
            live(File),
            col(ArtifactDerivative.kind) == DerivativeKind.METADATA,
            col(ArtifactDerivative.state) == DerivativeState.READY,
            col(ArtifactDerivative.output_json).contains(
                SlicedGeometry.GCODE_ONLY.value
            ),
        )
        .order_by(
            col(File.version).desc(), col(ArtifactDerivative.recipe_version).desc()
        )
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
