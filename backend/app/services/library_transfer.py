"""Portable, versioned library archive import/export."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from printstash_core.imports import CaptureContractError, CaptureManifestV2
from pydantic import BaseModel, ConfigDict, model_validator
from pydantic import Field as PydanticField
from sqlmodel import Session, delete, select

from app.core.time import utcnow
from app.db.models import (
    ArtifactProvenanceLink,
    Collection,
    CollectionTagLink,
    File,
    FileRevisionStatus,
    FileTagLink,
    FileType,
    Metadata,
    Model,
    ModelProvenanceField,
    ModelProvenanceSource,
    ModelSourceCover,
    ModelStar,
    ModelTagLink,
    MultipartModel,
    MultipartModelChoice,
    MultipartPart,
    PartGroup,
    PartOption,
    PrintJob,
    ProvenanceCapture,
    SavedView,
    StagingLease,
    Tag,
    User,
)
from app.db.scopes import live
from app.schemas.models import PartGroupWrite, PartOptionWrite
from app.services import (
    ingestion,
    model_views,
    part_options,
    provenance,
    rbac,
    source_covers,
    storage,
    taxonomy,
)
from app.services.artifact_content import ArtifactContentError, resolve
from app.services.jobs import registry
from app.services.storage_backend import get_backend

FORMAT = "printstash-library-v1"
# The library manifest remains v1 for compatibility; the provenance sidecar
# has its own explicit version because it carries a richer, exact snapshot.
PROVENANCE_FORMAT = "printstash-provenance-v2"
LEGACY_PROVENANCE_FORMAT = "printstash-provenance-v1"
# A portable archive contains one entry per Artifact plus manifest.json. The
# previous 20k ceiling let PrintStash export a library that the same version
# could not import. Keep a zip-bomb ceiling, but make it large enough for the
# reference large-library target and preflight exports against the same limits.
MAX_ENTRIES = 250_000
MAX_UNCOMPRESSED = 100 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 128 * 1024 * 1024
_HASH_CHUNK_SIZE = 1024 * 1024
_MAX_COVER_BYTES = 15 * 1024 * 1024


class PortableArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_id: int | None = None
    entry: str = PydanticField(min_length=1, max_length=1024)
    original_filename: str = PydanticField(min_length=1, max_length=255)
    file_type: Literal["stl", "3mf", "gcode", "obj", "step"]
    version: int = PydanticField(gt=0)
    size_bytes: int = PydanticField(ge=0)
    sha256: str = PydanticField(pattern=r"^[0-9a-fA-F]{64}$")
    revision_label: str | None = PydanticField(default=None, max_length=128)
    revision_status: (
        Literal["known_good", "needs_test", "failed", "archived"] | None
    ) = None
    revision_notes: str | None = None
    is_recommended: bool = False
    metadata: dict[str, Any] = PydanticField(default_factory=dict)
    tags: list[str] = PydanticField(default_factory=list)


class PortablePartOption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    artifact_source_id: int | None = None
    model_source_id: int | None = None
    name: str = PydanticField(min_length=1, max_length=255)
    is_default: bool = False

    @model_validator(mode="after")
    def exactly_one_target(self) -> "PortablePartOption":
        if (self.artifact_source_id is None) == (self.model_source_id is None):
            raise ValueError("part option requires exactly one target")
        return self


class PortablePartGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = PydanticField(min_length=1, max_length=255)
    options: list[PortablePartOption] = PydanticField(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_choices(self) -> "PortablePartGroup":
        if sum(option.is_default for option in self.options) != 1:
            raise ValueError("part group requires exactly one default")
        targets = [
            ("artifact", option.artifact_source_id)
            if option.artifact_source_id is not None
            else ("model", option.model_source_id)
            for option in self.options
        ]
        if len(targets) != len(set(targets)):
            raise ValueError("part group targets must be unique")
        names = [" ".join(option.name.split()).casefold() for option in self.options]
        if len(names) != len(set(names)):
            raise ValueError("part option names must be unique")
        return self


class PortableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_id: int
    name: str = PydanticField(min_length=1, max_length=255)
    slug: str | None = PydanticField(default=None, max_length=255)
    hash: str = PydanticField(pattern=r"^[0-9a-fA-F]{64}$")
    description: str | None = None
    source_url: str | None = PydanticField(default=None, max_length=2048)
    collection: str | None = PydanticField(default=None, max_length=512)
    collection_tags: list[str] = PydanticField(default_factory=list)
    tags: list[str] = PydanticField(default_factory=list)
    starred: bool = False
    artifacts: list[PortableArtifact] = PydanticField(default_factory=list)
    # None distinguishes legacy manifests from a deliberate empty replacement.
    part_groups: list[PortablePartGroup] | None = PydanticField(
        default=None, max_length=50
    )

    @model_validator(mode="after")
    def valid_part_groups(self) -> "PortableModel":
        if self.part_groups is None:
            return self
        artifact_ids = {
            artifact.source_id
            for artifact in self.artifacts
            if artifact.source_id is not None
        }
        chosen_artifact_ids = [
            option.artifact_source_id
            for group in self.part_groups
            for option in group.options
            if option.artifact_source_id is not None
        ]
        if not set(chosen_artifact_ids) <= artifact_ids or len(
            chosen_artifact_ids
        ) != len(set(chosen_artifact_ids)):
            raise ValueError("part options must reference unique model artifacts")
        names = [" ".join(group.name.split()).casefold() for group in self.part_groups]
        if len(names) != len(set(names)):
            raise ValueError("part group names must be unique")
        return self


class PortableMultipartChoice(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    model_source_id: int
    artifact_source_id: int | None = None
    label: str | None = PydanticField(default=None, max_length=128)


class PortableMultipartPart(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = PydanticField(min_length=1, max_length=128)
    # ``model_source_ids`` is the shape emitted by the first standalone
    # archive implementation.  Keep accepting it, but write the richer
    # explicit choice shape so file-pinned alternatives survive a round trip.
    model_source_ids: list[int] | None = PydanticField(
        default=None, min_length=1, max_length=100
    )
    choices: list[PortableMultipartChoice] | None = PydanticField(
        default=None, min_length=1, max_length=100
    )

    @model_validator(mode="after")
    def valid_shape(self) -> "PortableMultipartPart":
        if self.model_source_ids is None and self.choices is None:
            raise ValueError("multipart part requires choices")
        if self.model_source_ids is not None and self.choices is not None:
            raise ValueError("multipart part has multiple choice shapes")
        if self.model_source_ids is not None and len(self.model_source_ids) != len(
            set(self.model_source_ids)
        ):
            raise ValueError("multipart part models must be unique")
        return self


class PortableMultipartModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_id: int
    name: str = PydanticField(min_length=1, max_length=255)
    slug: str = PydanticField(min_length=1, max_length=255)
    description: str | None = None
    collection: str | None = PydanticField(default=None, max_length=512)
    cover_model_source_id: int | None = None
    parts: list[PortableMultipartPart] = PydanticField(default_factory=list)

    @model_validator(mode="after")
    def valid_part_names(self) -> "PortableMultipartModel":
        names = [" ".join(part.name.split()).casefold() for part in self.parts]
        if any(not name for name in names) or len(names) != len(set(names)):
            raise ValueError("multipart part names must be unique")
        return self


class PortableManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    format: Literal["printstash-library-v1"]
    exported_at: str | None = None
    models: list[PortableModel]
    print_jobs: list[dict[str, Any]] = PydanticField(default_factory=list)
    saved_views: list[dict[str, Any]] = PydanticField(default_factory=list)
    # Optional keeps archives produced before standalone multipart models fully
    # readable.
    multipart_models: list[PortableMultipartModel] = PydanticField(default_factory=list)

    @model_validator(mode="after")
    def unique_source_ids(self) -> "PortableManifest":
        model_ids = [model.source_id for model in self.models]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("duplicate model source_id")
        artifact_ids = [
            artifact.source_id
            for model in self.models
            for artifact in model.artifacts
            if artifact.source_id is not None
        ]
        if len(artifact_ids) != len(set(artifact_ids)):
            raise ValueError("duplicate artifact source_id")
        model_id_set = set(model_ids)
        chosen_model_ids = [
            option.model_source_id
            for model in self.models
            for group in model.part_groups or []
            for option in group.options
            if option.model_source_id is not None
        ]
        if not set(chosen_model_ids) <= model_id_set or len(chosen_model_ids) != len(
            set(chosen_model_ids)
        ):
            raise ValueError("part options must reference unique archive models")
        aggregate_ids = [row.source_id for row in self.multipart_models]
        if len(aggregate_ids) != len(set(aggregate_ids)):
            raise ValueError("duplicate multipart source_id")
        artifact_ids_by_model = {
            model.source_id: {
                artifact.source_id
                for artifact in model.artifacts
                if artifact.source_id is not None
            }
            for model in self.models
        }
        for aggregate in self.multipart_models:
            selected: list[tuple[int, int | None]] = []
            for part in aggregate.parts:
                if part.choices is not None:
                    for choice in part.choices:
                        if choice.model_source_id not in model_id_set:
                            raise ValueError(
                                "multipart models must reference archive models once"
                            )
                        if (
                            choice.artifact_source_id is not None
                            and choice.artifact_source_id
                            not in artifact_ids_by_model[choice.model_source_id]
                        ):
                            raise ValueError(
                                "multipart choices must reference archive artifacts"
                            )
                        selected.append(
                            (choice.model_source_id, choice.artifact_source_id)
                        )
                else:
                    assert part.model_source_ids is not None
                    if not set(part.model_source_ids) <= model_id_set:
                        raise ValueError(
                            "multipart models must reference archive models once"
                        )
                    selected.extend(
                        (model_id, None) for model_id in part.model_source_ids
                    )
            if len(selected) != len(set(selected)):
                raise ValueError("multipart models must reference archive choices once")
            if (
                aggregate.cover_model_source_id is not None
                and aggregate.cover_model_source_id
                not in {model_id for model_id, _artifact_id in selected}
            ):
                raise ValueError("multipart cover must reference an archive choice")
        return self


def _json_value(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def create_archive(session: Session, user: User) -> Path:
    visible_ids = model_views.accessible_live_model_ids_stmt(session, user)
    models = session.exec(
        select(Model)
        .where(Model.id.in_(visible_ids))  # type: ignore[union-attr]
        .order_by(Model.id.asc())  # type: ignore[attr-defined]
    ).all()
    collection_paths = {
        row.id: row.path
        for row in session.exec(
            select(Collection)
            .join(Model, Model.collection_id == Collection.id)  # type: ignore[arg-type]
            .where(Model.id.in_(visible_ids))  # type: ignore[union-attr]
            .distinct()
        ).all()
    }
    files = session.exec(
        select(File)
        .where(File.model_id.in_(visible_ids), live(File))  # type: ignore[union-attr]
        .order_by(File.model_id.asc(), File.version.asc())  # type: ignore[attr-defined]
    ).all()
    visible_model_ids = {int(model.id) for model in models}
    visible_file_ids = {int(file.id) for file in files}
    portable_groups: dict[int, dict[int, dict[str, object]]] = {}
    incomplete_group_ids: set[int] = set()
    for group, option in session.exec(
        select(PartGroup, PartOption)
        .join(PartOption, PartOption.part_group_id == PartGroup.id)
        .where(PartGroup.model_id.in_(visible_ids))  # type: ignore[union-attr]
        .order_by(PartGroup.sort_order.asc(), PartOption.sort_order.asc())  # type: ignore[attr-defined]
    ).all():
        assert group.id is not None
        if (
            option.file_id is not None
            and option.file_id not in visible_file_ids
            or option.model_id is not None
            and option.model_id not in visible_model_ids
        ):
            incomplete_group_ids.add(group.id)
            continue
        portable_group = portable_groups.setdefault(group.model_id, {}).setdefault(
            group.id, {"name": group.name, "options": []}
        )
        portable_option = {
            "name": option.name,
            "is_default": option.is_default,
        }
        if option.model_id is not None:
            portable_option["model_source_id"] = option.model_id
        else:
            portable_option["artifact_source_id"] = option.file_id
        portable_group["options"].append(portable_option)  # type: ignore[union-attr]
    metadata = {
        row.file_id: row
        for row in session.exec(
            select(Metadata)
            .join(File, File.id == Metadata.file_id)  # type: ignore[arg-type]
            .where(File.model_id.in_(visible_ids), live(File))  # type: ignore[union-attr]
        ).all()
    }
    tags_by_model: dict[int, list[str]] = {}
    for model_id, tag_name in session.exec(
        select(ModelTagLink.model_id, Tag.name)
        # SQLModel's column descriptors are typed as bool here, although
        # SQLAlchemy receives the expected expression at runtime.
        .join(Tag, Tag.id == ModelTagLink.tag_id)  # type: ignore[arg-type]
        .where(ModelTagLink.model_id.in_(visible_ids), live(Tag))  # type: ignore[union-attr]
        .order_by(ModelTagLink.model_id.asc(), Tag.name.asc())  # type: ignore[attr-defined]
    ).all():
        if model_id is not None:
            tags_by_model.setdefault(int(model_id), []).append(tag_name)
    tags_by_collection: dict[int, list[str]] = {}
    for collection_id, tag_name in session.exec(
        select(CollectionTagLink.collection_id, Tag.name)
        .join(Tag, Tag.id == CollectionTagLink.tag_id)
        .where(live(Tag))
        .order_by(CollectionTagLink.collection_id.asc(), Tag.name.asc())  # type: ignore[attr-defined]
    ).all():
        if collection_id is not None:
            tags_by_collection.setdefault(int(collection_id), []).append(tag_name)
    tags_by_file: dict[int, list[str]] = {}
    for file_id, tag_name in session.exec(
        select(FileTagLink.file_id, Tag.name)
        .join(Tag, Tag.id == FileTagLink.tag_id)
        .where(FileTagLink.file_id.in_([row.id for row in files]), live(Tag))  # type: ignore[union-attr]
        .order_by(FileTagLink.file_id.asc(), Tag.name.asc())  # type: ignore[attr-defined]
    ).all():
        if file_id is not None:
            tags_by_file.setdefault(int(file_id), []).append(tag_name)
    jobs = session.exec(
        select(PrintJob)
        .where(PrintJob.model_id.in_(visible_ids), live(PrintJob))  # type: ignore[union-attr]
        .order_by(PrintJob.id.asc())  # type: ignore[attr-defined]
    ).all()
    stars = set(
        session.exec(
            select(ModelStar.model_id).where(
                ModelStar.user_id == user.id,
                ModelStar.model_id.in_(visible_ids),  # type: ignore[union-attr]
            )
        ).all()
    )
    saved = session.exec(select(SavedView).where(SavedView.user_id == user.id)).all()

    manifest: dict[str, object] = {
        "format": FORMAT,
        "exported_at": utcnow().isoformat(),
        "models": [],
        "print_jobs": [],
        "saved_views": [
            {"name": row.name, "filters": json.loads(row.filters_json)} for row in saved
        ],
        "multipart_models": [],
    }
    files_by_model: dict[int, list[File]] = {}
    for row in files:
        files_by_model.setdefault(row.model_id, []).append(row)

    file_entries: list[tuple[File, str]] = []
    for model in models:
        if model.id is None:
            continue
        artifacts = []
        for artifact in files_by_model.get(model.id, []):
            if artifact.id is None:
                continue
            entry = (
                f"blobs/{model.hash}/{artifact.version}-{artifact.id}-"
                f"{Path(artifact.original_filename).name}"
            )
            file_entries.append((artifact, entry))
            md = metadata.get(artifact.id)
            artifacts.append(
                {
                    "source_id": artifact.id,
                    "entry": entry,
                    "original_filename": artifact.original_filename,
                    "file_type": artifact.file_type.value,
                    "version": artifact.version,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                    "revision_label": artifact.revision_label,
                    "revision_status": _json_value(artifact.revision_status),
                    "revision_notes": artifact.revision_notes,
                    "is_recommended": artifact.is_recommended,
                    "tags": tags_by_file.get(artifact.id, []),
                    "metadata": {
                        key: _json_value(value)
                        for key, value in md.model_dump(
                            # created_at is set fresh by Metadata's
                            # default_factory on import — carrying the source
                            # instance's ISO string through crashes Artifact
                            # persistence's SQLite datetime write.
                            exclude={"id", "file_id", "created_at"}
                        ).items()
                    }
                    if md
                    else {},
                }
            )
        manifest["models"].append(  # type: ignore[union-attr]
            {
                "source_id": model.id,
                "name": model.name,
                "slug": model.slug,
                "hash": model.hash,
                "description": model.description,
                "source_url": model.source_url,
                "collection": collection_paths.get(model.collection_id),
                "collection_tags": tags_by_collection.get(model.collection_id or 0, []),
                "tags": tags_by_model.get(model.id or 0, []),
                "starred": model.id in stars,
                "artifacts": artifacts,
                "part_groups": [
                    group
                    for group_id, group in portable_groups.get(model.id, {}).items()
                    if group_id not in incomplete_group_ids
                    and len(group["options"]) >= 1  # type: ignore[arg-type]
                ],
            }
        )
    aggregate_stmt = select(MultipartModel)
    if not user.is_superuser:
        aggregate_collection_ids = rbac.accessible_collection_ids(session, user)
        if not aggregate_collection_ids:
            aggregate_stmt = aggregate_stmt.where(MultipartModel.id == -1)
        else:
            aggregate_stmt = aggregate_stmt.where(
                MultipartModel.collection_id.in_(aggregate_collection_ids)  # type: ignore[union-attr]
            )
    aggregates = session.exec(aggregate_stmt.order_by(MultipartModel.id.asc())).all()  # type: ignore[attr-defined]
    aggregate_collection_paths = {
        row.id: row.path
        for row in session.exec(
            select(Collection).where(
                Collection.id.in_(  # type: ignore[union-attr]
                    [
                        row.collection_id
                        for row in aggregates
                        if row.collection_id is not None
                    ]
                )
            )
        ).all()
    }
    for aggregate in aggregates:
        if aggregate.id is None:
            continue
        portable_parts: list[dict[str, object]] = []
        parts = session.exec(
            select(MultipartPart)
            .where(MultipartPart.multipart_model_id == aggregate.id)
            .order_by(MultipartPart.sort_order.asc())  # type: ignore[attr-defined]
        ).all()
        for part in parts:
            choices = session.exec(
                select(MultipartModelChoice)
                .where(MultipartModelChoice.multipart_part_id == part.id)
                .order_by(MultipartModelChoice.sort_order.asc())  # type: ignore[attr-defined]
            ).all()
            portable_choices: list[dict[str, object]] = []
            for choice in choices:
                if choice.model_id not in visible_model_ids:
                    continue
                source_file_id = getattr(choice, "source_file_id", None)
                if (
                    source_file_id is not None
                    and source_file_id not in visible_file_ids
                ):
                    continue
                portable_choice: dict[str, object] = {
                    "model_source_id": int(choice.model_id),
                }
                if source_file_id is not None:
                    portable_choice["artifact_source_id"] = int(source_file_id)
                label = getattr(choice, "label", None)
                if label is not None:
                    portable_choice["label"] = label
                portable_choices.append(portable_choice)
            if portable_choices:
                portable_parts.append({"name": part.name, "choices": portable_choices})
        portable_aggregate: dict[str, object] = {
            "source_id": aggregate.id,
            "name": aggregate.name,
            "slug": aggregate.slug,
            "description": aggregate.description,
            "collection": aggregate_collection_paths.get(aggregate.collection_id),
            "parts": portable_parts,
        }
        if aggregate.cover_model_id in visible_model_ids and any(
            choice.get("model_source_id") == aggregate.cover_model_id
            for part in portable_parts
            for choice in part.get("choices", [])  # type: ignore[union-attr]
        ):
            portable_aggregate["cover_model_source_id"] = aggregate.cover_model_id
        manifest["multipart_models"].append(portable_aggregate)  # type: ignore[union-attr]
    for job in jobs:
        manifest["print_jobs"].append(  # type: ignore[union-attr]
            {
                "source_id": job.id,
                "model_source_id": job.model_id,
                "file_source_id": job.file_id,
                "remote_filename": job.remote_filename,
                "printer_name": job.printer_name,
                "state": job.state.value,
                "source": job.source,
                "filament_used_g": job.filament_used_g,
                "actual_duration_s": job.actual_duration_s,
                "cost": job.cost,
                "filament_g_effective": job.filament_g_effective,
                "started_at": _json_value(job.started_at),
                "finished_at": _json_value(job.finished_at),
            }
        )

    manifest_bytes = json.dumps(manifest, indent=2).encode("utf-8")
    provenance_models: list[dict[str, object]] = []
    cover_entries: list[tuple[ModelSourceCover, str]] = []
    for model in models:
        sources = session.exec(
            select(ModelProvenanceSource).where(
                ModelProvenanceSource.model_id == model.id
            )
        ).all()
        if not sources:
            continue
        portable_sources: list[dict[str, object]] = []
        for source in sources:
            fields = session.exec(
                select(ModelProvenanceField).where(
                    ModelProvenanceField.provenance_source_id == source.id
                )
            ).all()
            latest = session.exec(
                select(ProvenanceCapture)
                .where(ProvenanceCapture.provenance_source_id == source.id)
                .order_by(ProvenanceCapture.captured_at.desc())  # type: ignore[attr-defined]
            ).first()
            links = session.exec(
                select(ArtifactProvenanceLink)
                .where(ArtifactProvenanceLink.provenance_source_id == source.id)
                .order_by(ArtifactProvenanceLink.file_id.asc())  # type: ignore[attr-defined]
            ).all()
            # A source without a capture or an Artifact link has no portable
            # Artifact provenance to restore. Never invent an adapter version
            # for older hand-created source rows.
            if latest is None or not links:
                continue
            # ``captured_value_json`` is non-null for historical schema
            # reasons.  Rows created for an override before a provider has
            # captured the field use the legacy empty-string sentinel and a
            # null ``captured_at``.  Never put that sentinel in the portable
            # captured-field contract: it is not a captured value and strict
            # CaptureManifestV2 parsing correctly rejects empty strings.
            portable_fields: list[dict[str, object]] = []
            portable_overrides: list[dict[str, object]] = []
            for field in fields:
                if field.captured_at is None:
                    if field.user_override_set:
                        portable_overrides.append(
                            {
                                "field_name": field.field_name,
                                "user_value": json.loads(field.user_value_json)
                                if field.user_value_json is not None
                                else None,
                            }
                        )
                    continue
                portable_fields.append(
                    {
                        "field_name": field.field_name,
                        "captured_value": json.loads(field.captured_value_json),
                        "captured_origin": field.captured_origin,
                        "user_value": json.loads(field.user_value_json)
                        if field.user_value_json is not None
                        else None,
                        "user_override_set": field.user_override_set,
                    }
                )
            portable_source: dict[str, object] = {
                "source_id": source.id,
                "provider": source.provider,
                "source_item_id": source.source_item_id,
                "canonical_url": source.canonical_url,
                "source_revision": source.source_revision,
                "fields": portable_fields,
                "latest_capture": {
                    "adapter_version": latest.adapter_version,
                    "snapshot": json.loads(latest.snapshot_json),
                    "snapshot_sha256": latest.snapshot_sha256,
                },
                "artifact_links": [
                    {
                        "artifact_source_id": link.file_id,
                        "source_file_id": link.source_file_id,
                        "source_filename": link.source_filename,
                        "container_entry_path": link.container_entry_path,
                        "source_revision": link.source_revision,
                        "blob_sha256": link.blob_sha256,
                    }
                    for link in links
                ],
            }
            if any(
                not _safe_provenance_reference(link.container_entry_path)
                for link in links
            ):
                raise ValueError("portable_provenance_invalid")
            cover = session.exec(
                select(ModelSourceCover).where(
                    ModelSourceCover.provenance_source_id == source.id
                )
            ).first()
            if cover is not None:
                if cover.size_bytes < 0 or cover.size_bytes > _MAX_COVER_BYTES:
                    raise ValueError("archive_blob_hash_mismatch")
                cover_entry = f"covers/{model.id}-{source.id}.webp"
                cover_entries.append((cover, cover_entry))
                # The member is portable archive-relative metadata; the local
                # storage key is deliberately never exported.
                portable_source["cover"] = {
                    "entry": cover_entry,
                    "content_type": cover.content_type,
                    "size_bytes": cover.size_bytes,
                    "sha256": hashlib.sha256(
                        get_backend().read_bytes(cover.storage_key)
                    ).hexdigest(),
                }
            # Keep ordinary sidecars byte/schema-compatible with the original
            # format.  The optional key is only needed when a sparse field has
            # an explicit override that must survive transport.
            if portable_overrides:
                portable_source["overrides"] = portable_overrides
            portable_sources.append(portable_source)
        if portable_sources:
            provenance_models.append(
                {"model_source_id": model.id, "sources": portable_sources}
            )
    provenance_bytes = json.dumps(
        {"format": PROVENANCE_FORMAT, "models": provenance_models},
        separators=(",", ":"),
    ).encode("utf-8")
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise ValueError("archive_too_large")
    if len(provenance_bytes) > MAX_MANIFEST_BYTES:
        raise ValueError("archive_too_large")
    expected_size = (
        len(manifest_bytes)
        + len(provenance_bytes)
        + sum(row.size_bytes for row, _ in file_entries)
        + sum(row.size_bytes for row, _ in cover_entries)
    )
    if (
        len(file_entries) + len(cover_entries) + 2 > MAX_ENTRIES
        or expected_size > MAX_UNCOMPRESSED
    ):
        raise ValueError("archive_too_large")

    fd, filename = tempfile.mkstemp(suffix=".printstash.zip")
    try:
        # Keep and write through the exclusive mkstemp descriptor. Unlinking
        # the placeholder and reopening by name would create a race in which an
        # unrelated file at the random path could be truncated.
        with open(fd, "w+b", closefd=True) as output:
            with zipfile.ZipFile(
                output, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True
            ) as archive:
                actual_size = len(manifest_bytes)
                for artifact, entry in file_entries:
                    try:
                        with resolve(artifact).materialize() as source:
                            digest = hashlib.sha256()
                            artifact_size = 0
                            with (
                                source.open("rb") as source_file,
                                archive.open(
                                    entry, "w", force_zip64=True
                                ) as archive_entry,
                            ):
                                while chunk := source_file.read(_HASH_CHUNK_SIZE):
                                    artifact_size += len(chunk)
                                    if actual_size + artifact_size > MAX_UNCOMPRESSED:
                                        raise ValueError("archive_too_large")
                                    digest.update(chunk)
                                    archive_entry.write(chunk)
                    except ArtifactContentError as exc:
                        raise ValueError("archive_blob_hash_mismatch") from exc
                    if (
                        artifact_size != artifact.size_bytes
                        or digest.hexdigest() != artifact.sha256.lower()
                    ):
                        raise ValueError("archive_blob_hash_mismatch")
                    actual_size += artifact_size
                    if actual_size > MAX_UNCOMPRESSED:
                        raise ValueError("archive_too_large")
                for cover, entry in cover_entries:
                    data = get_backend().read_bytes(cover.storage_key)
                    digest = hashlib.sha256(data).hexdigest()
                    if len(data) != cover.size_bytes or digest != next(
                        source["cover"]["sha256"]
                        for model_data in provenance_models
                        for source in model_data["sources"]
                        if source.get("cover", {}).get("entry") == entry
                    ):
                        raise ValueError("archive_blob_hash_mismatch")
                    archive.writestr(entry, data)
                    actual_size += len(data)
                    if actual_size > MAX_UNCOMPRESSED:
                        raise ValueError("archive_too_large")
                archive.writestr("manifest.json", manifest_bytes)
                archive.writestr("provenance.json", provenance_bytes)
        return Path(filename)
    except Exception:
        Path(filename).unlink(missing_ok=True)
        raise


def _safe_entry(name: str) -> bool:
    path = PurePosixPath(name)
    return not path.is_absolute() and ".." not in path.parts and "\\" not in name


def _safe_provenance_reference(value: str | None) -> bool:
    """Allow only archive-relative link metadata, never URLs or credentials."""
    return value is None or (
        isinstance(value, str)
        and _safe_entry(value)
        and "://" not in value
        and "?" not in value
        and "#" not in value
        and "\x00" not in value
    )


def _validate_artifact_member(archive: zipfile.ZipFile, artifact: dict) -> None:
    """Validate one Artifact member without materializing its bytes in memory."""
    try:
        entry = str(artifact["entry"])
        expected_size = int(artifact["size_bytes"])
        expected_sha256 = str(artifact["sha256"]).lower()
        info = archive.getinfo(entry)
    except KeyError as exc:
        raise ValueError("archive_blob_missing") from exc
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_manifest") from exc

    if info.is_dir() or info.file_size != expected_size:
        raise ValueError("archive_blob_hash_mismatch")

    digest = hashlib.sha256()
    actual_size = 0
    with archive.open(info) as source:
        while chunk := source.read(_HASH_CHUNK_SIZE):
            actual_size += len(chunk)
            if actual_size > expected_size:
                raise ValueError("archive_blob_hash_mismatch")
            digest.update(chunk)
    if actual_size != expected_size or digest.hexdigest() != expected_sha256:
        raise ValueError("archive_blob_hash_mismatch")


def _read_provenance_sidecar(
    archive: zipfile.ZipFile, manifest: dict[str, Any]
) -> dict[str, Any] | None:
    """Validate the optional portable sidecar before any storage/DB write."""
    names = [info.filename for info in archive.infolist()]
    if names.count("manifest.json") != 1 or names.count("provenance.json") > 1:
        raise ValueError("portable_manifest_invalid")
    if "provenance.json" not in names:
        return None
    info = archive.getinfo("provenance.json")
    if info.is_dir() or info.file_size > MAX_MANIFEST_BYTES:
        raise ValueError("portable_provenance_invalid")
    try:
        raw = archive.read(info)
        sidecar = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("portable_provenance_invalid") from None
    if (
        not isinstance(sidecar, dict)
        or sidecar.get("format") not in {PROVENANCE_FORMAT, LEGACY_PROVENANCE_FORMAT}
        or not isinstance(sidecar.get("models"), list)
    ):
        raise ValueError("portable_provenance_invalid")
    manifest_ids = {row["source_id"] for row in manifest["models"]}
    sidecar_ids = [
        row.get("model_source_id") for row in sidecar["models"] if isinstance(row, dict)
    ]
    if (
        len(sidecar_ids) != len(sidecar["models"])
        or len(sidecar_ids) != len(set(sidecar_ids))
        or not set(sidecar_ids) <= manifest_ids
    ):
        raise ValueError("portable_provenance_invalid")
    return sidecar


def _validate_provenance_cover_members(
    archive: zipfile.ZipFile, sidecar: dict[str, Any] | None
) -> None:
    """Preflight portable cover relationships and bytes before any writes."""
    if sidecar is None:
        return
    seen_entries: set[str] = set()
    for model_row in sidecar["models"]:
        if not isinstance(model_row, dict) or not isinstance(
            model_row.get("sources"), list
        ):
            raise ValueError("portable_provenance_invalid")
        for source in model_row["sources"]:
            if not isinstance(source, dict) or "cover" not in source:
                continue
            cover = source["cover"]
            if not isinstance(cover, dict) or set(cover) != {
                "entry",
                "content_type",
                "size_bytes",
                "sha256",
            }:
                raise ValueError("portable_provenance_invalid")
            entry = cover["entry"]
            content_type = cover["content_type"]
            size_bytes = cover["size_bytes"]
            expected_sha256 = cover["sha256"]
            if (
                not isinstance(entry, str)
                or not _safe_entry(entry)
                or not entry.startswith("covers/")
                or not isinstance(content_type, str)
                or content_type != "image/webp"
                or not isinstance(size_bytes, int)
                or isinstance(size_bytes, bool)
                or size_bytes < 0
                or size_bytes > _MAX_COVER_BYTES
                or not isinstance(expected_sha256, str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256)
            ):
                raise ValueError("portable_provenance_invalid")
            if (
                entry in seen_entries
                or sum(info.filename == entry for info in archive.infolist()) != 1
            ):
                raise ValueError("portable_provenance_invalid")
            seen_entries.add(entry)
            try:
                info = archive.getinfo(entry)
            except KeyError as exc:
                raise ValueError("portable_provenance_invalid") from exc
            if info.is_dir() or info.file_size != size_bytes:
                raise ValueError("portable_provenance_invalid")
            digest = hashlib.sha256()
            actual_size = 0
            with archive.open(info) as stream:
                while chunk := stream.read(_HASH_CHUNK_SIZE):
                    actual_size += len(chunk)
                    if actual_size > size_bytes:
                        raise ValueError("portable_provenance_invalid")
                    digest.update(chunk)
            if (
                actual_size != size_bytes
                or digest.hexdigest() != expected_sha256.lower()
            ):
                raise ValueError("portable_provenance_invalid")


def _portable_v2_provenance_contexts(
    sidecar: dict[str, Any], manifest: dict[str, Any]
) -> dict[
    tuple[int, int], list[tuple[provenance.ProvenanceContext, dict[str, object]]]
]:
    """Build exact full-snapshot contexts from a v2 sidecar.

    Every sidecar relationship is checked against the library manifest before
    this function returns.  Each Artifact then receives the same full capture
    manifest, so importing one member cannot silently collapse a multi-file
    source snapshot to a one-file history.
    """
    artifacts = {
        (model["source_id"], artifact["source_id"]): artifact
        for model in manifest["models"]
        for artifact in model["artifacts"]
        if artifact.get("source_id") is not None
    }
    contexts: dict[
        tuple[int, int], list[tuple[provenance.ProvenanceContext, dict[str, object]]]
    ] = {}
    seen_sources: set[tuple[int, int]] = set()
    seen_links: set[tuple[int, int, str, str]] = set()
    base_source_keys = {
        "source_id",
        "provider",
        "source_item_id",
        "canonical_url",
        "source_revision",
        "fields",
        "latest_capture",
        "artifact_links",
    }
    link_keys = {
        "artifact_source_id",
        "source_file_id",
        "source_filename",
        "container_entry_path",
        "source_revision",
        "blob_sha256",
    }
    for model_row in sidecar["models"]:
        if set(model_row) != {"model_source_id", "sources"} or not isinstance(
            model_row["sources"], list
        ):
            raise ValueError("portable_provenance_invalid")
        model_source_id = model_row["model_source_id"]
        if not isinstance(model_source_id, int) or isinstance(model_source_id, bool):
            raise ValueError("portable_provenance_invalid")
        for source in model_row["sources"]:
            if not isinstance(source, dict) or not (
                set(source) == base_source_keys | {"cover"}
                or set(source) == base_source_keys | {"overrides"}
                or set(source) == base_source_keys | {"cover", "overrides"}
                or set(source) == base_source_keys
            ):
                raise ValueError("portable_provenance_invalid")
            source_id = source.get("source_id")
            if not isinstance(source_id, int) or isinstance(source_id, bool):
                raise ValueError("portable_provenance_invalid")
            source_key = (model_source_id, source_id)
            if source_key in seen_sources:
                raise ValueError("portable_provenance_invalid")
            seen_sources.add(source_key)
            capture = source.get("latest_capture")
            if (
                not isinstance(capture, dict)
                or set(capture) != {"adapter_version", "snapshot", "snapshot_sha256"}
                or not isinstance(capture["snapshot"], dict)
                or not isinstance(capture["snapshot_sha256"], str)
                or not isinstance(source.get("fields"), list)
                or not isinstance(source.get("artifact_links"), list)
            ):
                raise ValueError("portable_provenance_invalid")
            snapshot = capture["snapshot"]
            snapshot_keys = {
                "provider",
                "canonical_url",
                "source_item_id",
                "source_revision",
                "tags",
                "fields",
                "files",
            }
            if set(snapshot) != snapshot_keys:
                raise ValueError("portable_provenance_invalid")
            encoded_snapshot = json.dumps(
                snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            if (
                not re.fullmatch(r"[0-9a-fA-F]{64}", capture["snapshot_sha256"])
                or hashlib.sha256(encoded_snapshot).hexdigest()
                != capture["snapshot_sha256"].lower()
                or snapshot["provider"] != source.get("provider")
                or snapshot["canonical_url"] != source.get("canonical_url")
                or snapshot["source_item_id"] != source.get("source_item_id")
                or snapshot["source_revision"] != source.get("source_revision")
                or not isinstance(snapshot["tags"], list)
                or not isinstance(snapshot["fields"], dict)
                or not isinstance(snapshot["files"], list)
                or not snapshot["files"]
            ):
                raise ValueError("portable_provenance_invalid")

            fields: dict[str, dict[str, object]] = {}
            overrides: dict[str, object] = {}
            for field in source["fields"]:
                if (
                    not isinstance(field, dict)
                    or set(field)
                    != {
                        "field_name",
                        "captured_value",
                        "captured_origin",
                        "user_value",
                        "user_override_set",
                    }
                    or not isinstance(field["field_name"], str)
                    or field["field_name"] in fields
                    or not isinstance(field["user_override_set"], bool)
                ):
                    raise ValueError("portable_provenance_invalid")
                fields[field["field_name"]] = {
                    "value": field["captured_value"],
                    "origin": field["captured_origin"],
                }
                if field["user_override_set"]:
                    overrides[field["field_name"]] = field["user_value"]
            raw_overrides = source.get("overrides", [])
            if not isinstance(raw_overrides, list):
                raise ValueError("portable_provenance_invalid")
            for override in raw_overrides:
                if (
                    not isinstance(override, dict)
                    or set(override) != {"field_name", "user_value"}
                    or not isinstance(override["field_name"], str)
                    or override["field_name"] not in provenance.PROVENANCE_FIELD_NAMES
                    or override["field_name"] in overrides
                ):
                    raise ValueError("portable_provenance_invalid")
                overrides[override["field_name"]] = override["user_value"]
            if set(fields) != set(snapshot["fields"]):
                raise ValueError("portable_provenance_invalid")
            for field_name, value in snapshot["fields"].items():
                if (
                    not isinstance(value, dict)
                    or set(value) != {"origin", "value"}
                    or fields[field_name] != value
                ):
                    raise ValueError("portable_provenance_invalid")

            links = source["artifact_links"]
            if not links or any(
                not isinstance(link, dict) or set(link) != link_keys for link in links
            ):
                raise ValueError("portable_provenance_invalid")
            raw_files: list[dict[str, object]] = []
            artifact_file_ids: dict[int, str] = {}
            matched_artifacts: set[int] = set()
            for snapshot_file in snapshot["files"]:
                if (
                    not isinstance(snapshot_file, dict)
                    or set(snapshot_file)
                    != {"source_selection_id", "source_file_id", "source_filename"}
                    or not isinstance(snapshot_file["source_file_id"], str)
                    or not isinstance(snapshot_file["source_selection_id"], str)
                    or not isinstance(snapshot_file["source_filename"], str)
                ):
                    raise ValueError("portable_provenance_invalid")
                matches = [
                    link
                    for link in links
                    if link["source_file_id"] == snapshot_file["source_file_id"]
                    and link["source_filename"] == snapshot_file["source_filename"]
                ]
                if len(matches) != 1:
                    raise ValueError("portable_provenance_invalid")
                link = matches[0]
                artifact_source_id = link["artifact_source_id"]
                artifact = artifacts.get((model_source_id, artifact_source_id))
                if (
                    not isinstance(artifact_source_id, int)
                    or isinstance(artifact_source_id, bool)
                    or artifact is None
                    or artifact_source_id in matched_artifacts
                ):
                    raise ValueError("portable_provenance_invalid")
                matched_artifacts.add(artifact_source_id)
                artifact_file_ids[artifact_source_id] = snapshot_file["source_file_id"]
                try:
                    storage.validate_leaf_name(snapshot_file["source_filename"])
                except storage.UnsafeStorageComponent:
                    raise ValueError("portable_provenance_invalid") from None
                raw_files.append(
                    {
                        "id": snapshot_file["source_file_id"],
                        "name": snapshot_file["source_filename"],
                        "file_type": artifact["file_type"],
                        "size": artifact["size_bytes"],
                    }
                )
            if matched_artifacts != {link["artifact_source_id"] for link in links}:
                raise ValueError("portable_provenance_invalid")
            raw_manifest = {
                "schema_version": 2,
                "kind": "model_files",
                "source": {
                    "provider": snapshot["provider"],
                    "canonical_url": snapshot["canonical_url"],
                    "source_item_id": snapshot["source_item_id"],
                    "source_revision": snapshot["source_revision"],
                    "adapter_version": capture["adapter_version"],
                    "tags": snapshot["tags"],
                    "fields": snapshot["fields"],
                },
                "files": raw_files,
                "selected_ids": [file["id"] for file in raw_files],
            }
            try:
                capture_manifest = CaptureManifestV2.from_dict(raw_manifest)
            except (CaptureContractError, TypeError, ValueError):
                raise ValueError("portable_provenance_invalid") from None
            for link in links:
                artifact_source_id = link["artifact_source_id"]
                artifact = artifacts[(model_source_id, artifact_source_id)]
                if (
                    link["source_file_id"] != artifact_file_ids[artifact_source_id]
                    or not isinstance(link["source_filename"], str)
                    or link["source_file_id"] is None
                    or link["container_entry_path"] is not None
                    and not isinstance(link["container_entry_path"], str)
                    or not _safe_provenance_reference(link["container_entry_path"])
                    or link["source_revision"] is not None
                    and not isinstance(link["source_revision"], str)
                    or link["source_revision"] != source["source_revision"]
                    or not isinstance(link["blob_sha256"], str)
                    or link["blob_sha256"].lower() != artifact["sha256"].lower()
                ):
                    raise ValueError("portable_provenance_invalid")
                link_key = (
                    model_source_id,
                    artifact_source_id,
                    link["source_file_id"],
                    link["source_filename"],
                )
                if link_key in seen_links:
                    raise ValueError("portable_provenance_invalid")
                seen_links.add(link_key)
                contexts.setdefault((model_source_id, artifact_source_id), []).append(
                    (
                        provenance.ProvenanceContext(
                            manifest=capture_manifest,
                            source_file_id=link["source_file_id"],
                            source_filename=link["source_filename"],
                            container_entry_path=link["container_entry_path"],
                            blob_sha256=artifact["sha256"],
                            source_selection_id=artifact_file_ids[artifact_source_id],
                            actor_id=None,
                        ),
                        overrides,
                    )
                )
    return contexts


def _portable_provenance_contexts(
    sidecar: dict[str, Any] | None, manifest: dict[str, Any]
) -> dict[
    tuple[int, int], list[tuple[provenance.ProvenanceContext, dict[str, object]]]
]:
    """Strictly turn a sidecar into per-Artifact provenance contexts.

    This is deliberately a preflight-only transformation: the public
    provenance seam remains the sole owner of source/capture/link writes.
    """
    if sidecar is None:
        return {}
    if sidecar.get("format") == PROVENANCE_FORMAT:
        return _portable_v2_provenance_contexts(sidecar, manifest)
    artifacts = {
        (model["source_id"], artifact["source_id"]): artifact
        for model in manifest["models"]
        for artifact in model["artifacts"]
        if artifact.get("source_id") is not None
    }
    contexts: dict[
        tuple[int, int], list[tuple[provenance.ProvenanceContext, dict[str, object]]]
    ] = {}
    seen_links: set[tuple[int, int, str | None, str]] = set()
    for model_row in sidecar["models"]:
        if set(model_row) != {"model_source_id", "sources"} or not isinstance(
            model_row["sources"], list
        ):
            raise ValueError("portable_provenance_invalid")
        model_source_id = model_row["model_source_id"]
        if not isinstance(model_source_id, int) or isinstance(model_source_id, bool):
            raise ValueError("portable_provenance_invalid")
        for source in model_row["sources"]:
            expected_source_keys = {
                "source_id",
                "provider",
                "source_item_id",
                "canonical_url",
                "source_revision",
                "fields",
                "latest_capture",
                "artifact_links",
            }
            if not isinstance(source, dict) or not (
                set(source) == expected_source_keys
                or set(source) == expected_source_keys | {"overrides"}
            ):
                raise ValueError("portable_provenance_invalid")
            capture = source["latest_capture"]
            if (
                not isinstance(capture, dict)
                or set(capture) != {"adapter_version", "snapshot"}
                or not isinstance(capture["snapshot"], dict)
                or not isinstance(source["fields"], list)
                or not isinstance(source["artifact_links"], list)
            ):
                raise ValueError("portable_provenance_invalid")
            fields: dict[str, dict[str, object]] = {}
            overrides: dict[str, object] = {}
            for field in source["fields"]:
                if (
                    not isinstance(field, dict)
                    or set(field)
                    != {
                        "field_name",
                        "captured_value",
                        "captured_origin",
                        "user_value",
                        "user_override_set",
                    }
                    or not isinstance(field["field_name"], str)
                    or field["field_name"] in fields
                    or not isinstance(field["user_override_set"], bool)
                ):
                    raise ValueError("portable_provenance_invalid")
                fields[field["field_name"]] = {
                    "value": field["captured_value"],
                    "origin": field["captured_origin"],
                }
                if field["user_override_set"]:
                    overrides[field["field_name"]] = field["user_value"]
            raw_overrides = source.get("overrides", [])
            if not isinstance(raw_overrides, list):
                raise ValueError("portable_provenance_invalid")
            for override in raw_overrides:
                if (
                    not isinstance(override, dict)
                    or set(override) != {"field_name", "user_value"}
                    or not isinstance(override["field_name"], str)
                    or override["field_name"] not in provenance.PROVENANCE_FIELD_NAMES
                    or override["field_name"] in overrides
                ):
                    raise ValueError("portable_provenance_invalid")
                overrides[override["field_name"]] = override["user_value"]
            for link in source["artifact_links"]:
                expected_link_keys = {
                    "artifact_source_id",
                    "source_file_id",
                    "source_filename",
                    "container_entry_path",
                    "source_revision",
                    "blob_sha256",
                }
                if not isinstance(link, dict) or set(link) != expected_link_keys:
                    raise ValueError("portable_provenance_invalid")
                artifact_source_id = link["artifact_source_id"]
                artifact = artifacts.get((model_source_id, artifact_source_id))
                if (
                    artifact is None
                    or not isinstance(artifact_source_id, int)
                    or isinstance(artifact_source_id, bool)
                    or not isinstance(link["source_filename"], str)
                    or link["source_file_id"] is not None
                    and not isinstance(link["source_file_id"], str)
                    or link["container_entry_path"] is not None
                    and not isinstance(link["container_entry_path"], str)
                    or not _safe_provenance_reference(link["container_entry_path"])
                    or link["source_revision"] is not None
                    and not isinstance(link["source_revision"], str)
                    or not isinstance(link["blob_sha256"], str)
                    or link["blob_sha256"].lower() != artifact["sha256"].lower()
                ):
                    raise ValueError("portable_provenance_invalid")
                try:
                    storage.validate_leaf_name(link["source_filename"])
                except storage.UnsafeStorageComponent:
                    raise ValueError("portable_provenance_invalid") from None
                fallback_id = f"portable-artifact-{artifact_source_id}"
                source_file_id = link["source_file_id"] or fallback_id
                link_key = (
                    model_source_id,
                    artifact_source_id,
                    link["source_file_id"],
                    link["source_filename"],
                )
                if link_key in seen_links:
                    raise ValueError("portable_provenance_invalid")
                seen_links.add(link_key)
                raw_manifest = {
                    "schema_version": 2,
                    "kind": "model_files",
                    "source": {
                        "provider": source["provider"],
                        "canonical_url": source["canonical_url"],
                        "source_item_id": source["source_item_id"],
                        "source_revision": source["source_revision"],
                        "adapter_version": capture["adapter_version"],
                        "fields": fields,
                    },
                    "files": [
                        {
                            "id": source_file_id,
                            "name": link["source_filename"],
                            "file_type": artifact["file_type"],
                            "size": artifact["size_bytes"],
                        }
                    ],
                    "selected_ids": [source_file_id],
                }
                try:
                    capture_manifest = CaptureManifestV2.from_dict(raw_manifest)
                except (CaptureContractError, TypeError, ValueError):
                    raise ValueError("portable_provenance_invalid") from None
                contexts.setdefault((model_source_id, artifact_source_id), []).append(
                    (
                        provenance.ProvenanceContext(
                            manifest=capture_manifest,
                            source_file_id=link["source_file_id"] or fallback_id,
                            source_filename=link["source_filename"],
                            container_entry_path=link["container_entry_path"],
                            blob_sha256=artifact["sha256"],
                            source_selection_id=source_file_id,
                            actor_id=None,
                        ),
                        overrides,
                    )
                )
    return contexts


def _restore_portable_covers(
    session: Session,
    archive: zipfile.ZipFile,
    sidecar: dict[str, Any] | None,
    source_models: dict[int, Model],
    user: User,
) -> list[source_covers.SourceCoverWrite]:
    """Restore sidecar covers through the private source-cover seam."""
    if sidecar is None:
        return []
    writes: list[source_covers.SourceCoverWrite] = []
    for model_row in sidecar["models"]:
        model = source_models.get(model_row["model_source_id"])
        if model is None or model.id is None:
            raise ValueError("portable_provenance_invalid")
        for source_data in model_row["sources"]:
            cover_data = source_data.get("cover")
            if cover_data is None:
                continue
            source_query = select(ModelProvenanceSource).where(
                ModelProvenanceSource.model_id == model.id,
                ModelProvenanceSource.provider == source_data["provider"],
                ModelProvenanceSource.canonical_url == source_data["canonical_url"],
            )
            source_item_id = source_data["source_item_id"]
            source_query = source_query.where(
                ModelProvenanceSource.source_item_id.is_(None)
                if source_item_id is None
                else ModelProvenanceSource.source_item_id == source_item_id
            )
            sources = session.exec(source_query).all()
            if len(sources) != 1 or sources[0].id is None:
                raise ValueError("portable_provenance_invalid")
            source = sources[0]
            data = archive.read(cover_data["entry"])
            writes.append(
                source_covers.put(
                    session,
                    get_backend(),
                    provenance_source_id=source.id,
                    actor_id=user.id,
                    data=data,
                    content_type=cover_data["content_type"],
                )
            )
    return writes


def _legacy_multipart_slug(model: Model) -> str:
    """Return a reserved, stable slug for a composition materialized from v1."""
    # Model hashes are unique in a vault, so this is stable across retries and
    # cannot collide between generated compositions.  It is intentionally a
    # slug rather than a description marker: descriptions remain user data.
    return f"legacy-parts-{model.hash}"


def _restore_legacy_multipart_model(
    session: Session,
    user: User,
    model: Model,
    model_data: dict[str, Any],
    source_models: dict[int, Model],
    source_files: dict[int, File],
) -> None:
    """Expose old nested part groups as a standalone composition.

    Releases before standalone multipart models stored the composition below a
    Model.  Keep those rows (they are still used by the old read/restore seam),
    but also materialize the same roles into the current independent tables so
    users can find and edit the composition in the new UI.  A source file is
    retained as choice metadata when that column is available; the owning
    Model remains the only owner of the Artifact.
    """
    groups = model_data.get("part_groups")
    if not groups:
        return
    reserved_slug = _legacy_multipart_slug(model)
    aggregate = session.exec(
        select(MultipartModel).where(MultipartModel.slug == reserved_slug)
    ).first()
    if aggregate is None:
        # If a manually-created row occupies the reserved hash slug, use a
        # deterministic model-id suffix.  This remains stable on retries while
        # avoiding any overwrite of the unrelated composition.
        fallback_slug = f"{reserved_slug}-{model.id}"
        aggregate = session.exec(
            select(MultipartModel).where(MultipartModel.slug == fallback_slug)
        ).first()
        base_slug = fallback_slug if aggregate is not None else reserved_slug
        aggregate_slug = storage.ensure_unique_slug(
            base_slug,
            lambda value: (
                session.exec(
                    select(MultipartModel.id).where(MultipartModel.slug == value)
                ).first()
                is not None
            ),
        )
        if aggregate is None:
            aggregate = MultipartModel(
                name=f"{model.name} (Parts)",
                slug=aggregate_slug,
                description=model.description,
                collection_id=model.collection_id,
                created_by=user.id,
                updated_by=user.id,
            )
            session.add(aggregate)
            session.flush()
    else:
        # Re-import updates the generated composition while retaining the
        # user's current name/description and collection choices.
        aggregate.updated_by = user.id
        session.add(aggregate)
        session.flush()

    assert aggregate.id is not None
    # Import is a replacement for this source composition, not an append.  The
    # reserved slug makes retries idempotent even after a process died mid-import.
    old_parts = session.exec(
        select(MultipartPart).where(MultipartPart.multipart_model_id == aggregate.id)
    ).all()
    for old_part in old_parts:
        old_choices = session.exec(
            select(MultipartModelChoice).where(
                MultipartModelChoice.multipart_part_id == old_part.id
            )
        ).all()
        for old_choice in old_choices:
            session.delete(old_choice)
        session.delete(old_part)
    session.flush()
    _materialize_legacy_parts(session, aggregate, groups, source_models, source_files)


def _materialize_legacy_parts(
    session: Session,
    aggregate: MultipartModel,
    groups: list[dict[str, Any]],
    source_models: dict[int, Model],
    source_files: dict[int, File],
) -> None:
    for part_order, group in enumerate(groups):
        part_name = " ".join(str(group["name"]).split())
        part = MultipartPart(
            multipart_model_id=aggregate.id,
            name=part_name,
            name_key=part_name.casefold(),
            sort_order=part_order,
        )
        session.add(part)
        session.flush()
        assert part.id is not None
        for choice_order, option in enumerate(group.get("options", [])):
            source_file = (
                source_files.get(int(option["artifact_source_id"]))
                if option.get("artifact_source_id") is not None
                else None
            )
            member = (
                source_models.get(int(option["model_source_id"]))
                if option.get("model_source_id") is not None
                else source_file and session.get(Model, source_file.model_id)
            )
            if member is None or member.id is None:
                continue
            # ``source_file_id``/``label`` were added with the standalone
            # compatibility migration.  Keeping this guarded also lets an
            # operator run the import code against a pre-migration test DB.
            kwargs: dict[str, object] = {
                "multipart_model_id": aggregate.id,
                "multipart_part_id": part.id,
                "model_id": member.id,
                "sort_order": choice_order,
            }
            if hasattr(MultipartModelChoice, "source_file_id"):
                kwargs["source_file_id"] = source_file.id if source_file else None
            if hasattr(MultipartModelChoice, "label"):
                kwargs["label"] = option["name"]
            session.add(MultipartModelChoice(**kwargs))
    session.flush()


def _portable_aggregate_matches(
    session: Session,
    aggregate: MultipartModel,
    aggregate_data: dict[str, Any],
    source_models: dict[int, Model],
    source_files: dict[int, File],
) -> bool:
    """Check an existing slug before allowing an archive replacement.

    Slugs are user-editable, so a matching slug is not proof that a row came
    from this archive.  Compare the complete imported composition first; a
    mismatch is handled as an additive import with a unique slug.
    """
    if aggregate.name != aggregate_data.get(
        "name"
    ) or aggregate.description != aggregate_data.get("description"):
        return False
    cover_source_id = aggregate_data.get("cover_model_source_id")
    expected_cover = (
        source_models.get(cover_source_id) if cover_source_id is not None else None
    )
    if aggregate.cover_model_id != (
        expected_cover.id if expected_cover is not None else None
    ):
        return False
    parts = session.exec(
        select(MultipartPart)
        .where(MultipartPart.multipart_model_id == aggregate.id)
        .order_by(MultipartPart.sort_order.asc())  # type: ignore[attr-defined]
    ).all()
    portable_parts = aggregate_data.get("parts", [])
    if len(parts) != len(portable_parts):
        return False
    for part, part_data in zip(parts, portable_parts, strict=True):
        if part.name != part_data.get("name"):
            return False
        choices = session.exec(
            select(MultipartModelChoice)
            .where(MultipartModelChoice.multipart_part_id == part.id)
            .order_by(MultipartModelChoice.sort_order.asc())  # type: ignore[attr-defined]
        ).all()
        expected: list[tuple[int, int | None, str | None]] = []
        if part_data.get("choices") is not None:
            for choice in part_data["choices"]:
                model = source_models.get(choice["model_source_id"])
                source_file = (
                    source_files.get(choice["artifact_source_id"])
                    if choice.get("artifact_source_id") is not None
                    else None
                )
                if model is None or model.id is None:
                    return False
                expected.append(
                    (
                        int(model.id),
                        int(source_file.id)
                        if source_file is not None and source_file.id is not None
                        else None,
                        choice.get("label"),
                    )
                )
        else:
            for source_id in part_data.get("model_source_ids", []):
                model = source_models.get(source_id)
                if model is None or model.id is None:
                    return False
                expected.append((int(model.id), None, None))
        actual = [
            (
                int(choice.model_id),
                getattr(choice, "source_file_id", None),
                getattr(choice, "label", None),
            )
            for choice in choices
        ]
        if actual != expected:
            return False
    return True


def import_archive(session: Session, archive_path: Path, user: User) -> dict[str, int]:
    assert user.id is not None
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if (
            len(infos) > MAX_ENTRIES
            or sum(item.file_size for item in infos) > MAX_UNCOMPRESSED
        ):
            raise ValueError("archive_too_large")
        if any(not _safe_entry(item.filename) for item in infos):
            raise ValueError("unsafe_archive_path")
        try:
            manifest_info = archive.getinfo("manifest.json")
            if manifest_info.is_dir() or manifest_info.file_size > MAX_MANIFEST_BYTES:
                raise ValueError("portable_manifest_invalid")
            with archive.open(manifest_info) as manifest_file:
                manifest_bytes = manifest_file.read(MAX_MANIFEST_BYTES + 1)
            if len(manifest_bytes) > MAX_MANIFEST_BYTES:
                raise ValueError("portable_manifest_invalid")
            raw_manifest = json.loads(manifest_bytes)
            if not isinstance(raw_manifest, dict):
                raise ValueError("portable_manifest_invalid")
            # Empty/missing top-level data is the signature of a pre-standalone
            # archive when nested part_groups are present.  Treat both forms as
            # legacy so those compositions are materialized instead of hidden.
            has_standalone_multipart_models = bool(raw_manifest.get("multipart_models"))
            manifest = PortableManifest.model_validate(raw_manifest).model_dump(
                mode="json"
            )
        except ValueError as exc:
            if str(exc) == "portable_manifest_invalid":
                raise
            raise ValueError("portable_manifest_invalid") from exc
        except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("portable_manifest_invalid") from exc
        if manifest.get("format") != FORMAT or not isinstance(
            manifest.get("models"), list
        ):
            raise ValueError("unsupported_archive_format")
        sidecar = _read_provenance_sidecar(archive, manifest)
        _validate_provenance_cover_members(archive, sidecar)

        # Validate every blob before first database/storage write.
        for model_data in manifest["models"]:
            for artifact in model_data.get("artifacts", []):
                try:
                    artifact["original_filename"] = storage.validate_leaf_name(
                        artifact["original_filename"]
                    )
                except (KeyError, TypeError, storage.UnsafeStorageComponent) as exc:
                    raise ValueError("portable_manifest_invalid") from exc
                _validate_artifact_member(archive, artifact)
        provenance_contexts = _portable_provenance_contexts(sidecar, manifest)

        created_models = created_files = skipped_files = provenance_conflicts = 0
        source_models: dict[int, Model] = {}
        source_files: dict[int, File] = {}
        with tempfile.TemporaryDirectory(prefix="printstash-import-") as tempdir:
            for model_data in manifest["models"]:
                model = session.exec(
                    select(Model).where(Model.hash == model_data["hash"], live(Model))
                ).first()
                if model is None:
                    collection = (
                        taxonomy.resolve_or_create_collection(
                            session, model_data.get("collection") or ""
                        )
                        if model_data.get("collection")
                        else None
                    )
                    base_slug = storage.slugify(str(model_data["name"]))
                    generated_slug = storage.ensure_unique_slug(
                        base_slug,
                        lambda value: (
                            session.exec(
                                select(Model.id).where(Model.slug == value)
                            ).first()
                            is not None
                        ),
                    )
                    model = Model(
                        name=model_data["name"],
                        slug=generated_slug,
                        hash=model_data["hash"],
                        description=model_data.get("description"),
                        source_url=model_data.get("source_url"),
                        collection_id=collection.id if collection else None,
                        created_by=user.id,
                    )
                    session.add(model)
                    session.commit()
                    session.refresh(model)
                    created_models += 1
                assert model.id is not None
                source_models[model_data["source_id"]] = model
                if model.collection_id is not None:
                    collection_tags = taxonomy.resolve_or_create_tags(
                        session, model_data.get("collection_tags", [])
                    )
                    existing_collection_tag_ids = set(
                        session.exec(
                            select(CollectionTagLink.tag_id).where(
                                CollectionTagLink.collection_id == model.collection_id
                            )
                        ).all()
                    )
                    for tag in collection_tags:
                        if tag.id not in existing_collection_tag_ids:
                            session.add(
                                CollectionTagLink(
                                    collection_id=model.collection_id, tag_id=tag.id
                                )
                            )
                resolved_tags = taxonomy.resolve_or_create_tags(
                    session, model_data.get("tags", [])
                )
                existing_tag_ids = set(
                    session.exec(
                        select(ModelTagLink.tag_id).where(
                            ModelTagLink.model_id == model.id
                        )
                    ).all()
                )
                for tag in resolved_tags:
                    if tag.id not in existing_tag_ids:
                        session.add(ModelTagLink(model_id=model.id, tag_id=tag.id))
                session.commit()
                for artifact_data in sorted(
                    model_data.get("artifacts", []), key=lambda row: row["version"]
                ):
                    existing = session.exec(
                        select(File).where(
                            File.model_id == model.id,
                            File.sha256 == artifact_data["sha256"],
                            live(File),
                        )
                    ).first()
                    if existing:
                        source_files[
                            artifact_data.get("source_id", artifact_data["version"])
                        ] = existing
                        for context, overrides in provenance_contexts.get(
                            (model_data["source_id"], artifact_data.get("source_id")),
                            [],
                        ):
                            merge = provenance.attach_existing_artifact(
                                session, existing, context, imported_overrides=overrides
                            )
                            provenance_conflicts += len(
                                merge.conflicting_override_fields
                            )
                        skipped_files += 1
                        artifact_tags = taxonomy.resolve_or_create_tags(
                            session, artifact_data.get("tags", [])
                        )
                        existing_file_tag_ids = set(
                            session.exec(
                                select(FileTagLink.tag_id).where(
                                    FileTagLink.file_id == existing.id
                                )
                            ).all()
                        )
                        for tag in artifact_tags:
                            if tag.id not in existing_file_tag_ids:
                                session.add(
                                    FileTagLink(file_id=existing.id, tag_id=tag.id)
                                )
                        session.commit()
                        continue
                    staged = (
                        Path(tempdir)
                        / f"{model.id}-{artifact_data['version']}-{Path(artifact_data['original_filename']).name}"
                    )
                    with (
                        archive.open(artifact_data["entry"]) as src,
                        staged.open("xb") as dst,
                    ):
                        shutil.copyfileobj(src, dst)
                    file_row = ingestion.persist_artifact(
                        session,
                        model=model,
                        staged_path=staged,
                        original_filename=artifact_data["original_filename"],
                        file_type=FileType(artifact_data["file_type"]),
                        blob_hash=artifact_data["sha256"],
                        meta=artifact_data.get("metadata", {}),
                        thumb_bytes=None,
                        overwrite_thumbnail=False,
                        revision_label=artifact_data.get("revision_label"),
                        revision_status=FileRevisionStatus(
                            artifact_data["revision_status"]
                        )
                        if artifact_data.get("revision_status")
                        else None,
                        revision_notes=artifact_data.get("revision_notes"),
                        is_recommended=artifact_data.get("is_recommended", False),
                    )
                    source_files[
                        artifact_data.get("source_id", artifact_data["version"])
                    ] = file_row
                    artifact_tags = taxonomy.resolve_or_create_tags(
                        session, artifact_data.get("tags", [])
                    )
                    for tag in artifact_tags:
                        session.add(FileTagLink(file_id=file_row.id, tag_id=tag.id))
                    for context, overrides in provenance_contexts.get(
                        (model_data["source_id"], artifact_data.get("source_id")), []
                    ):
                        merge = provenance.attach_existing_artifact(
                            session, file_row, context, imported_overrides=overrides
                        )
                        provenance_conflicts += len(merge.conflicting_override_fields)
                    created_files += 1
                if model_data.get("starred"):
                    exists = session.exec(
                        select(ModelStar).where(
                            ModelStar.user_id == user.id,
                            ModelStar.model_id == model.id,
                        )
                    ).first()
                    if exists is None:
                        session.add(ModelStar(user_id=user.id, model_id=model.id))
                        session.commit()

            # Standalone multipart compositions are independent of Models.  An
            # archive from before this field simply yields an empty list here.
            for aggregate_data in manifest.get("multipart_models", []):
                collection = (
                    taxonomy.resolve_or_create_collection(
                        session, aggregate_data.get("collection") or ""
                    )
                    if aggregate_data.get("collection")
                    else None
                )
                imported_slug = storage.slugify(str(aggregate_data["slug"]))
                if not imported_slug:
                    imported_slug = "multipart-model"
                existing = session.exec(
                    select(MultipartModel).where(MultipartModel.slug == imported_slug)
                ).first()
                if existing is not None and _portable_aggregate_matches(
                    session,
                    existing,
                    aggregate_data,
                    source_models,
                    source_files,
                ):
                    # This is an idempotent retry of the same archive.  Do not
                    # churn part/choice rows or overwrite user data.
                    continue
                # A matching slug with different data belongs to another
                # composition.  Import additively under a sanitized unique
                # slug rather than deleting that composition.
                if existing is not None:
                    existing = None
                if existing is None:
                    # A caller may have deleted a prior aggregate in this
                    # session while its parts remain in SQLAlchemy's identity
                    # map.  SQLite can reuse those ids on the insert below;
                    # expunge only orphaned multipart identities to avoid
                    # replacement warnings and stale reads.
                    for identity in list(session.identity_map.values()):
                        if isinstance(identity, MultipartModelChoice):
                            owner_id = identity.__dict__.get("multipart_model_id")
                        elif isinstance(identity, MultipartPart):
                            owner_id = identity.__dict__.get("multipart_model_id")
                        else:
                            continue
                        if owner_id is None:
                            session.expunge(identity)
                            continue
                        if session.get(MultipartModel, owner_id) is None:
                            session.expunge(identity)
                    aggregate_slug = storage.ensure_unique_slug(
                        imported_slug,
                        lambda value: (
                            session.exec(
                                select(MultipartModel.id).where(
                                    MultipartModel.slug == value
                                )
                            ).first()
                            is not None
                        ),
                    )
                    existing = MultipartModel(
                        name=aggregate_data["name"],
                        slug=aggregate_slug,
                        description=aggregate_data.get("description"),
                        collection_id=collection.id if collection else None,
                        created_by=user.id,
                        updated_by=user.id,
                    )
                    session.add(existing)
                    # Keep the aggregate and its composition in the same
                    # transaction.  A malformed part must never leave an
                    # empty top-level row behind.
                    session.flush()
                aggregate_id = int(existing.id)
                session.exec(
                    delete(MultipartModelChoice).where(
                        MultipartModelChoice.multipart_model_id == aggregate_id
                    )
                )
                session.exec(
                    delete(MultipartPart).where(
                        MultipartPart.multipart_model_id == aggregate_id
                    )
                )
                session.flush()
                used_models: set[int] = set()
                for part_order, part_data in enumerate(aggregate_data.get("parts", [])):
                    choice_rows: list[tuple[int, int | None, str | None]] = []
                    explicit_choices = part_data.get("choices")
                    if explicit_choices is not None:
                        for choice in explicit_choices:
                            source_model = source_models.get(choice["model_source_id"])
                            source_file = (
                                source_files.get(choice["artifact_source_id"])
                                if choice.get("artifact_source_id") is not None
                                else None
                            )
                            if source_model is None or source_model.id is None:
                                continue
                            if (
                                choice.get("artifact_source_id") is not None
                                and source_file is None
                            ):
                                continue
                            choice_rows.append(
                                (
                                    int(source_model.id),
                                    int(source_file.id)
                                    if source_file and source_file.id is not None
                                    else None,
                                    choice.get("label"),
                                )
                            )
                    else:
                        # Archives emitted by the initial standalone format
                        # only carried model ids and had a global no-duplicate
                        # member rule.
                        for source_id in part_data.get("model_source_ids", []):
                            source_model = source_models.get(source_id)
                            if (
                                source_model is not None
                                and source_model.id is not None
                                and source_model.id not in used_models
                            ):
                                choice_rows.append((int(source_model.id), None, None))
                    if not choice_rows:
                        continue
                    part_row = MultipartPart(
                        multipart_model_id=aggregate_id,
                        name=part_data["name"],
                        name_key=part_data["name"].casefold(),
                        sort_order=part_order,
                    )
                    session.add(part_row)
                    session.flush()
                    assert part_row.id is not None
                    for model_order, (model_id, source_file_id, label) in enumerate(
                        choice_rows
                    ):
                        kwargs: dict[str, object] = {
                            "multipart_model_id": aggregate_id,
                            "multipart_part_id": part_row.id,
                            "model_id": model_id,
                            "sort_order": model_order,
                        }
                        if hasattr(MultipartModelChoice, "source_file_id"):
                            kwargs["source_file_id"] = source_file_id
                        if hasattr(MultipartModelChoice, "label"):
                            kwargs["label"] = label
                        session.add(MultipartModelChoice(**kwargs))
                        used_models.add(model_id)
                cover_source_id = aggregate_data.get("cover_model_source_id")
                cover_model = (
                    source_models.get(cover_source_id)
                    if cover_source_id is not None
                    else None
                )
                existing.cover_model_id = (
                    int(cover_model.id)
                    if cover_model is not None
                    and cover_model.id is not None
                    and cover_model.id in used_models
                    else None
                )
                session.commit()

            # A v1 archive has no top-level ``multipart_models`` member and
            # stores composition below each Model.  Materialize that legacy
            # shape into the current standalone tables, while retaining the
            # legacy rows below for backwards-compatible restore/export.  A
            # modern archive may still carry those rows for compatibility, but
            # its explicit top-level composition is authoritative and must not
            # be duplicated here.
            if not has_standalone_multipart_models:
                for model_data in manifest["models"]:
                    if model_data.get("part_groups") is None:
                        continue
                    _restore_legacy_multipart_model(
                        session,
                        user,
                        source_models[model_data["source_id"]],
                        model_data,
                        source_models,
                        source_files,
                    )
                session.commit()

            # Model-target groups are restored only after every archive Model
            # exists, so forward references do not depend on manifest ordering.
            for model_data in manifest["models"]:
                if model_data.get("part_groups") is None:
                    continue
                model = source_models[model_data["source_id"]]
                part_options.replace_for_model(
                    session,
                    model.id,
                    [
                        PartGroupWrite(
                            name=group["name"],
                            options=[
                                PartOptionWrite(
                                    file_id=(
                                        source_files[option["artifact_source_id"]].id
                                        if option["artifact_source_id"] is not None
                                        else None
                                    ),
                                    model_id=(
                                        source_models[option["model_source_id"]].id
                                        if option["model_source_id"] is not None
                                        else None
                                    ),
                                    name=option["name"],
                                    is_default=option["is_default"],
                                )
                                for option in group["options"]
                            ],
                        )
                        for group in model_data["part_groups"]
                    ],
                )

        imported_jobs = 0
        for job_data in manifest.get("print_jobs", []):
            model = source_models.get(job_data.get("model_source_id"))
            file_row = source_files.get(job_data.get("file_source_id"))
            if model is None or file_row is None:
                continue
            assert model.id is not None and file_row.id is not None
            started_at = (
                datetime.fromisoformat(job_data["started_at"])
                if job_data.get("started_at")
                else None
            )
            duplicate = session.exec(
                select(PrintJob).where(
                    PrintJob.model_id == model.id,
                    PrintJob.file_id == file_row.id,
                    PrintJob.remote_filename == job_data["remote_filename"],
                    PrintJob.started_at == started_at,
                )
            ).first()
            if duplicate:
                continue
            session.add(
                PrintJob(
                    model_id=model.id,
                    file_id=file_row.id,
                    remote_filename=job_data["remote_filename"],
                    printer_name=job_data.get("printer_name"),
                    state=job_data["state"],
                    source=job_data.get("source") or "archive",
                    filament_used_g=job_data.get("filament_used_g"),
                    actual_duration_s=job_data.get("actual_duration_s"),
                    cost=job_data.get("cost"),
                    filament_g_effective=job_data.get("filament_g_effective"),
                    started_at=started_at,
                    finished_at=(
                        datetime.fromisoformat(job_data["finished_at"])
                        if job_data.get("finished_at")
                        else None
                    ),
                )
            )
            imported_jobs += 1
        for saved_data in manifest.get("saved_views", []):
            existing = session.exec(
                select(SavedView).where(
                    SavedView.user_id == user.id, SavedView.name == saved_data["name"]
                )
            ).first()
            if existing is None:
                session.add(
                    SavedView(
                        user_id=user.id,
                        name=saved_data["name"],
                        filters_json=json.dumps(saved_data.get("filters", {})),
                    )
                )
        cover_writes = _restore_portable_covers(
            session, archive, sidecar, source_models, user
        )
        try:
            session.commit()
        except Exception:
            session.rollback()
            for write in cover_writes:
                source_covers.rollback_after_commit_failure(
                    session, get_backend(), write
                )
            raise
        result = {
            "created_models": created_models,
            "created_files": created_files,
            "skipped_files": skipped_files,
            "imported_jobs": imported_jobs,
        }
        # Keep the long-standing import result shape for ordinary/legacy
        # archives, while exposing only safe conflict counts when a portable
        # override actually lost to an existing local override.
        if provenance_conflicts:
            result["provenance_conflicts"] = provenance_conflicts
        return result


def run_import_job(
    *, job_id: str, archive_path: Path, user_id: int, session_factory
) -> None:
    """Durable job boundary for portable imports; partial progress remains visible."""
    registry.update(job_id, state="running", stage="ingesting")
    try:
        with session_factory.scoped_session() as session:
            user = session.get(User, user_id)
            if user is None:
                raise ValueError("user_not_found")
            result = import_archive(session, archive_path, user)
            lease = session.exec(
                select(StagingLease).where(StagingLease.background_job_id == job_id)
            ).first()
            if lease is not None:
                session.delete(lease)
                session.commit()
        registry.finish(
            job_id,
            state="completed",
            completion="complete",
            result=result,
            processed=result["created_files"] + result["skipped_files"],
            total=result["created_files"] + result["skipped_files"],
            succeeded=result["created_files"],
            skipped=result["skipped_files"],
        )
        archive_path.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001 - durable job boundary
        registry.finish(job_id, state="failed", error=str(exc), retryable=True)
