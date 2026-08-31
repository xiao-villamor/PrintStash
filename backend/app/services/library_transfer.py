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
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    ArtifactProvenanceLink,
    Collection,
    File,
    FileRevisionStatus,
    FileType,
    Metadata,
    Model,
    ModelProvenanceField,
    ModelProvenanceSource,
    ModelSourceCover,
    ModelStar,
    ModelTagLink,
    PrintJob,
    ProvenanceCapture,
    SavedView,
    StagingLease,
    Tag,
    User,
)
from app.db.scopes import live
from app.services import (
    ingestion,
    model_views,
    provenance,
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


class PortableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_id: int
    name: str = PydanticField(min_length=1, max_length=255)
    slug: str | None = PydanticField(default=None, max_length=255)
    hash: str = PydanticField(pattern=r"^[0-9a-fA-F]{64}$")
    description: str | None = None
    source_url: str | None = PydanticField(default=None, max_length=2048)
    collection: str | None = PydanticField(default=None, max_length=512)
    tags: list[str] = PydanticField(default_factory=list)
    starred: bool = False
    artifacts: list[PortableArtifact] = PydanticField(default_factory=list)


class PortableManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    format: Literal["printstash-library-v1"]
    exported_at: str | None = None
    models: list[PortableModel]
    print_jobs: list[dict[str, Any]] = PydanticField(default_factory=list)
    saved_views: list[dict[str, Any]] = PydanticField(default_factory=list)

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
                "tags": tags_by_model.get(model.id or 0, []),
                "starred": model.id in stars,
                "artifacts": artifacts,
            }
        )
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
            manifest = PortableManifest.model_validate_json(manifest_bytes).model_dump(
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
