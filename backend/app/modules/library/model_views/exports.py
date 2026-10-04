"""Library metadata export projections."""

from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from typing import Any

from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import (
    SENTINEL_MODEL_HASH,
    ArtifactProvenanceLink,
    Collection,
    File,
    FileType,
    Metadata,
    Model,
    ModelProvenanceSource,
    ModelSourceCover,
    ModelTagLink,
    ProvenanceCapture,
    Tag,
    User,
)
from app.db.scopes import live
from app.modules.printing.costing import cost_profiles
from app.schemas.models import (
    FileRead,
)

from .access import _apply_model_access
from .projections import _file_tag_names, metadata_read, thumb_url

_EXPORT_CSV_FIELDS = [
    "model_id",
    "model_name",
    "model_slug",
    "model_source_url",
    "collection",
    "tags",
    "file_id",
    "file_type",
    "version",
    "original_filename",
    "size_bytes",
    "sha256",
    "revision_label",
    "revision_status",
    "revision_notes",
    "is_recommended",
    "uploaded_at",
    "slicer_name",
    "slicer_version",
    "printer_model",
    "nozzle_diameter_mm",
    "layer_height_mm",
    "first_layer_height_mm",
    "infill_percent",
    "wall_loops",
    "top_shell_layers",
    "bottom_shell_layers",
    "support_material",
    "nozzle_temperature_c",
    "bed_temperature_c",
    "estimated_time_s",
    "filament_weight_g",
    "filament_length_mm",
    "filament_cost",
    "material_type",
    "material_brand",
    "bbox_x_mm",
    "bbox_y_mm",
    "bbox_z_mm",
    "volume_mm3",
    "triangle_count",
    "volume_state",
    "volume_unit",
    "volume_method",
    "volume_cause",
]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def export_payload(session: Session, user: User) -> dict:
    stmt = (
        select(Model)
        .where(live(Model))
        .where(Model.hash != SENTINEL_MODEL_HASH)
        .order_by(Model.name.asc())  # type: ignore[attr-defined]
    )
    stmt = _apply_model_access(stmt, session, user)
    model_rows = session.exec(stmt).all()
    model_ids = [m.id for m in model_rows if m.id is not None]
    provenance_by_model: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if model_ids:
        source_rows = session.exec(
            select(ModelProvenanceSource)
            .where(ModelProvenanceSource.model_id.in_(model_ids))  # type: ignore[union-attr]
            .order_by(
                ModelProvenanceSource.model_id.asc(), ModelProvenanceSource.id.asc()
            )  # type: ignore[attr-defined]
        ).all()
        source_ids = [row.id for row in source_rows if row.id is not None]
        captures_by_source: dict[int, list[ProvenanceCapture]] = defaultdict(list)
        links_by_source: dict[int, list[ArtifactProvenanceLink]] = defaultdict(list)
        covers_by_source: dict[int, ModelSourceCover] = {}
        if source_ids:
            for row in session.exec(
                select(ProvenanceCapture)
                .where(ProvenanceCapture.provenance_source_id.in_(source_ids))  # type: ignore[union-attr]
                .order_by(ProvenanceCapture.captured_at.desc())  # type: ignore[attr-defined]
            ).all():
                captures_by_source[row.provenance_source_id].append(row)
            for row in session.exec(
                select(ArtifactProvenanceLink)
                .where(ArtifactProvenanceLink.provenance_source_id.in_(source_ids))  # type: ignore[union-attr]
                .order_by(ArtifactProvenanceLink.file_id.asc())  # type: ignore[attr-defined]
            ).all():
                links_by_source[row.provenance_source_id].append(row)
            covers_by_source = {
                row.provenance_source_id: row
                for row in session.exec(
                    select(ModelSourceCover).where(
                        ModelSourceCover.provenance_source_id.in_(source_ids)  # type: ignore[union-attr]
                    )
                ).all()
            }
        for source in source_rows:
            # `id` is the primary key and `model_id` is NOT NULL, and these rows came
            # straight out of a SELECT — the same holds for `link.file_id` below. The
            # guards that used to stand here could not fire on any row this loop can
            # see, so they were statements no test could ever reach.
            artifacts = []
            for link in links_by_source[source.id]:
                artifacts.append(
                    {
                        "artifact_id": link.file_id,
                        "artifact_api_ref": f"/api/v1/files/{link.file_id}/download",
                        "source_file_id": link.source_file_id,
                        "source_filename": link.source_filename,
                        "source_revision": link.source_revision,
                        "blob_sha256": link.blob_sha256,
                    }
                )
            captures = [
                {
                    "snapshot_sha256": capture.snapshot_sha256,
                    "adapter_version": capture.adapter_version,
                    "source_revision": capture.source_revision,
                    "captured_at": capture.captured_at.isoformat(),
                }
                for capture in captures_by_source[source.id]
            ]
            cover = covers_by_source.get(source.id)
            cover_summary = None
            if cover is not None:
                cover_summary = {
                    "api_ref": f"/api/v1/models/{source.model_id}/provenance/{source.id}/cover",
                    "content_api_ref": f"/api/v1/models/{source.model_id}/provenance/{source.id}/cover/content",
                    "content_type": cover.content_type,
                    "size_bytes": cover.size_bytes,
                    "updated_at": cover.updated_at.isoformat(),
                }
            provenance_by_model[source.model_id].append(
                {
                    "id": source.id,
                    "provider": source.provider,
                    "source_item_id": source.source_item_id,
                    "canonical_url": source.canonical_url,
                    "source_revision": source.source_revision,
                    "tags": json.loads(source.tags_json),
                    "captures": captures,
                    "artifacts": artifacts,
                    "cover": cover_summary,
                }
            )
    if not model_ids:
        models = []
        file_count = 0
    else:
        collection_ids = {
            m.collection_id for m in model_rows if m.collection_id is not None
        }
        collections = {}
        if collection_ids:
            collections = {
                c.id: c.path
                for c in session.exec(
                    select(Collection).where(Collection.id.in_(collection_ids))  # type: ignore[union-attr]
                ).all()
                if c.id is not None
            }

        tags_by_model: dict[int, list[str]] = defaultdict(list)
        tag_rows = session.exec(
            select(ModelTagLink.model_id, Tag.name)
            .join(Tag, Tag.id == ModelTagLink.tag_id)
            .where(ModelTagLink.model_id.in_(model_ids))  # type: ignore[union-attr]
            .order_by(ModelTagLink.model_id.asc(), Tag.name.asc())  # type: ignore[attr-defined]
        ).all()
        for model_id, tag_name in tag_rows:
            if model_id is not None:
                tags_by_model[int(model_id)].append(tag_name)

        files_by_model: dict[int, list[dict]] = defaultdict(list)
        gcode_counts: dict[int, int] = defaultdict(int)
        profiles = cost_profiles(session)
        file_rows = session.exec(
            select(File, Metadata)
            .where(File.model_id.in_(model_ids))  # type: ignore[union-attr]
            .where(live(File))
            .outerjoin(Metadata, Metadata.file_id == File.id)
            .order_by(File.model_id.asc(), File.version.asc())  # type: ignore[attr-defined]
        ).all()
        file_tags_by_id = _file_tag_names(
            session,
            [
                file_row.id
                for file_row, _metadata in file_rows
                if file_row.id is not None
            ],
        )
        for file_row, metadata in file_rows:
            gcode_revision_number = None
            if file_row.file_type == FileType.GCODE:
                gcode_counts[file_row.model_id] += 1
                gcode_revision_number = gcode_counts[file_row.model_id]
            files_by_model[file_row.model_id].append(
                FileRead(
                    id=file_row.id,  # type: ignore[arg-type]
                    model_id=file_row.model_id,
                    original_filename=file_row.original_filename,
                    file_type=file_row.file_type,
                    version=file_row.version,
                    gcode_revision_number=gcode_revision_number,
                    size_bytes=file_row.size_bytes,
                    sha256=file_row.sha256,
                    revision_label=file_row.revision_label,
                    revision_status=file_row.revision_status,
                    revision_notes=file_row.revision_notes,
                    is_recommended=file_row.is_recommended,
                    is_external=file_row.is_external,
                    uploaded_at=file_row.uploaded_at,
                    tags=file_tags_by_id.get(file_row.id, []),
                    metadata=metadata_read(session, metadata, profiles)
                    if metadata
                    else None,
                ).model_dump(mode="json")
            )

        models = [
            {
                "id": model.id,
                "name": model.name,
                "slug": model.slug,
                "hash": model.hash,
                "collection": collections.get(model.collection_id),
                "collection_id": model.collection_id,
                "description": model.description,
                "source_url": model.source_url,
                "tags": tags_by_model.get(model.id or 0, []),
                "thumbnail_url": thumb_url(model),
                "created_at": model.created_at.isoformat(),
                "updated_at": model.updated_at.isoformat(),
                "files": files_by_model.get(model.id or 0, []),
                "provenance": {
                    "schema_version": 2,
                    "sources": provenance_by_model.get(model.id or 0, []),
                },
            }
            for model in model_rows
        ]
        file_count = sum(len(model["files"]) for model in models)

    return {
        "export_version": 2,
        "app": {"name": settings.app_name, "version": settings.app_version},
        "generated_at": utcnow().isoformat(),
        "contents": {
            "kind": "metadata_only",
            "includes": [
                "models",
                "collections",
                "tags",
                "stored file metadata",
                "slicer/mesh metadata",
                "G-code revision labels and outcomes",
                "provenance/source summaries and API references",
            ],
            "excludes": ["raw STL/3MF/G-code blobs", "secrets", "printer credentials"],
        },
        "counts": {"models": len(models), "files": file_count},
        "models": models,
    }


def _csv_cell(value: Any) -> Any:
    """Render a CSV cell, preserving a legitimate ``0`` / ``False``.

    A plain ``value or ""`` collapses real zeros — 0 % infill (vase mode), a
    0 °C unheated bed, 0 top-shell layers (open-top print) — into blanks. Only
    ``None`` (genuinely absent) should become an empty cell.
    """
    return "" if value is None else value


def export_csv(payload: dict) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=_EXPORT_CSV_FIELDS)
    writer.writeheader()
    for model in payload["models"]:
        tags = ",".join(model["tags"])
        for file_row in model["files"]:
            metadata = file_row.get("metadata") or {}
            volume = metadata["volume_measurement"] if metadata else None
            writer.writerow(
                {
                    "model_id": model["id"],
                    "model_name": model["name"],
                    "model_slug": model["slug"],
                    "model_source_url": model.get("source_url") or "",
                    "collection": model.get("collection") or "",
                    "tags": tags,
                    "file_id": file_row["id"],
                    "file_type": file_row["file_type"],
                    "version": file_row["version"],
                    "original_filename": file_row["original_filename"],
                    "size_bytes": file_row["size_bytes"],
                    "sha256": file_row["sha256"],
                    "revision_label": file_row.get("revision_label") or "",
                    "revision_status": file_row.get("revision_status") or "",
                    "revision_notes": file_row.get("revision_notes") or "",
                    "is_recommended": file_row["is_recommended"],
                    "uploaded_at": file_row["uploaded_at"],
                    "slicer_name": _csv_cell(metadata.get("slicer_name")),
                    "slicer_version": _csv_cell(metadata.get("slicer_version")),
                    "printer_model": _csv_cell(metadata.get("printer_model")),
                    "nozzle_diameter_mm": _csv_cell(metadata.get("nozzle_diameter_mm")),
                    "layer_height_mm": _csv_cell(metadata.get("layer_height_mm")),
                    "first_layer_height_mm": _csv_cell(
                        metadata.get("first_layer_height_mm")
                    ),
                    "infill_percent": _csv_cell(metadata.get("infill_percent")),
                    "wall_loops": _csv_cell(metadata.get("wall_loops")),
                    "top_shell_layers": _csv_cell(metadata.get("top_shell_layers")),
                    "bottom_shell_layers": _csv_cell(
                        metadata.get("bottom_shell_layers")
                    ),
                    "support_material": _csv_cell(metadata.get("support_material")),
                    "nozzle_temperature_c": _csv_cell(
                        metadata.get("nozzle_temperature_c")
                    ),
                    "bed_temperature_c": _csv_cell(metadata.get("bed_temperature_c")),
                    "estimated_time_s": _csv_cell(metadata.get("estimated_time_s")),
                    "filament_weight_g": _csv_cell(metadata.get("filament_weight_g")),
                    "filament_length_mm": _csv_cell(metadata.get("filament_length_mm")),
                    "filament_cost": _csv_cell(metadata.get("filament_cost")),
                    "material_type": _csv_cell(metadata.get("material_type")),
                    "material_brand": _csv_cell(metadata.get("material_brand")),
                    "bbox_x_mm": _csv_cell(metadata.get("bbox_x_mm")),
                    "bbox_y_mm": _csv_cell(metadata.get("bbox_y_mm")),
                    "bbox_z_mm": _csv_cell(metadata.get("bbox_z_mm")),
                    "volume_mm3": _csv_cell(metadata.get("volume_mm3")),
                    "triangle_count": _csv_cell(metadata.get("triangle_count")),
                    "volume_state": _csv_cell(
                        volume["state"] if volume is not None else None
                    ),
                    "volume_unit": _csv_cell(
                        volume["unit"] if volume is not None else None
                    ),
                    "volume_method": _csv_cell(
                        volume["method"] if volume is not None else None
                    ),
                    "volume_cause": _csv_cell(
                        volume["cause"] if volume is not None else None
                    ),
                }
            )
    return out.getvalue()
