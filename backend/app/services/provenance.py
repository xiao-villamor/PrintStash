"""Persistence owner for structured capture provenance.

This module deliberately owns identity, canonical snapshot bytes, field
precedence, and Artifact links.  It never writes blobs and never commits: the
caller controls the surrounding transaction, including ingestion rollback.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, Mapping
from urllib.parse import urlsplit, urlunsplit

from printstash_core.imports import CapturedField, CaptureManifestV2
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    ArtifactProvenanceLink,
    CollectionRole,
    File,
    Model,
    ModelProvenanceField,
    ModelProvenanceSource,
    ModelSourceCover,
    ProvenanceCapture,
    User,
)
from app.schemas.provenance import PROVENANCE_FIELD_NAMES
from app.services.rbac import effective_collection_role, role_allows
from app.services.storage_backend import get_backend
from app.services.storage_deletion import enqueue_owned_key

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# The first provenance schema made ``captured_value_json`` non-null, so an
# override created before a provider captured that field still needs a
# database placeholder.  Portable serialization must not expose this legacy
# compatibility value as captured data; ``captured_at is None`` is the
# authoritative sparse-field marker.
_ABSENT_CAPTURED_VALUE = ""
_ABSENT_CAPTURED_ORIGIN = "inferred"


def _normalise_text(value: str) -> str:
    return unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")


def _bounded(value: str | None, maximum: int, name: str) -> str | None:
    if value is None:
        return None
    value = _normalise_text(value)
    if not value or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise ValueError(f"invalid_{name}")
    return value


def _validated_sha256(value: str) -> str:
    value = value.lower()
    if not _SHA256.fullmatch(value):
        raise ValueError("invalid_blob_sha256")
    return value


def canonicalize_url(value: str) -> str:
    """Return the public, stable page URL with fragment and query discarded."""
    parsed = urlsplit(_normalise_text(value).strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("canonical_url_must_be_http_url")
    if parsed.username or parsed.password:
        raise ValueError("canonical_url_must_not_contain_credentials")
    host = parsed.hostname.lower()
    if parsed.port:
        host = f"{host}:{parsed.port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), host, path, "", ""))


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _field_snapshot(value: CapturedField) -> dict[str, str]:
    return {"origin": value.origin, "value": _normalise_text(value.value)}


def normalized_snapshot(manifest: CaptureManifestV2) -> dict:
    """The exact allowlisted structure that is serialized and hashed."""
    return {
        "provider": _bounded(manifest.source.provider, 64, "provider"),
        "canonical_url": canonicalize_url(manifest.source.canonical_url),
        "source_item_id": _bounded(
            manifest.source.source_item_id, 255, "source_item_id"
        ),
        "source_revision": _bounded(
            manifest.source.source_revision, 255, "source_revision"
        ),
        "tags": list(manifest.source.tags),
        "fields": {
            name: _field_snapshot(value)
            for name, value in sorted(manifest.source.fields.items())
        },
        "files": [
            {
                "source_selection_id": _normalise_text(item.id),
                "source_file_id": _normalise_text(item.id),
                "source_filename": _normalise_text(item.name),
            }
            for item in sorted(manifest.files, key=lambda item: item.id)
        ],
    }


def snapshot_json(manifest: CaptureManifestV2) -> str:
    return _canonical_json(normalized_snapshot(manifest))


def snapshot_sha256(manifest: CaptureManifestV2) -> str:
    return hashlib.sha256(snapshot_json(manifest).encode("utf-8")).hexdigest()


def identity_key(manifest: CaptureManifestV2) -> str:
    stable = manifest.source.source_item_id or canonicalize_url(
        manifest.source.canonical_url
    )
    return _identity_key(manifest.source.provider, stable)


def _identity_key(provider: str, stable: str) -> str:
    value = _canonical_json(
        {
            "provider": _bounded(provider, 64, "provider"),
            "identity": _normalise_text(stable),
        }
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_values(
    manifest: CaptureManifestV2,
) -> tuple[str, str | None, str, str | None]:
    provider = _bounded(manifest.source.provider, 64, "provider")
    assert provider is not None
    return (
        provider,
        _bounded(manifest.source.source_item_id, 255, "source_item_id"),
        canonicalize_url(manifest.source.canonical_url),
        _bounded(manifest.source.source_revision, 255, "source_revision"),
    )


def import_key(
    manifest: CaptureManifestV2,
    *,
    source_file_id: str | None,
    source_filename: str,
    blob_sha256: str,
) -> str:
    file_identity = _bounded(source_file_id, 255, "source_file_id") or _bounded(
        source_filename, 512, "source_filename"
    )
    value = _canonical_json(
        {
            "identity_key": identity_key(manifest),
            "file": file_identity,
            "blob_sha256": _validated_sha256(blob_sha256),
        }
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CaptureUpsertResult:
    source: ModelProvenanceSource
    capture: ProvenanceCapture
    created_capture: bool


@dataclass(frozen=True)
class ProvenancePreflightResult:
    status: Literal["not_found", "reusable", "trashed"]
    link: ArtifactProvenanceLink | None = None
    model_id: int | None = None
    file_id: int | None = None


@dataclass(frozen=True)
class PortableProvenanceMergeResult:
    link: ArtifactProvenanceLink
    imported_override_fields: tuple[str, ...]
    conflicting_override_fields: tuple[str, ...]


@dataclass(frozen=True)
class ProvenanceContext:
    manifest: CaptureManifestV2
    source_file_id: str | None
    source_filename: str
    source_selection_id: str | None = None
    container_entry_path: str | None = None
    blob_sha256: str | None = None
    inbox_item_id: int | None = None
    actor_id: int | None = None

    def __post_init__(self) -> None:
        _bounded(self.source_file_id, 255, "source_file_id")
        _bounded(self.source_filename, 512, "source_filename")
        _bounded(self.source_selection_id, 512, "source_selection_id")
        _bounded(self.container_entry_path, 1024, "container_entry_path")
        if self.blob_sha256 is not None:
            _validated_sha256(self.blob_sha256)


def effective_value(row: ModelProvenanceField):
    """Return user value even when it is deliberately JSON empty/null."""
    value_json = (
        row.user_value_json if row.user_override_set else row.captured_value_json
    )
    assert value_json is not None
    return json.loads(value_json)


def _find_source(
    session: Session, model_id: int, key: str
) -> ModelProvenanceSource | None:
    return session.exec(
        select(ModelProvenanceSource).where(
            ModelProvenanceSource.model_id == model_id,
            ModelProvenanceSource.identity_key == key,
        )
    ).first()


def _merge_sources(
    session: Session, target: ModelProvenanceSource, obsolete: ModelProvenanceSource
) -> ModelProvenanceSource:
    """Merge the URL fallback row into the stable row and its source cover."""
    if target.id == obsolete.id:
        return target
    assert target.id is not None and obsolete.id is not None
    target_fields = {
        row.field_name: row
        for row in session.exec(
            select(ModelProvenanceField).where(
                ModelProvenanceField.provenance_source_id == target.id
            )
        ).all()
    }
    if target.tags_json == "[]" and obsolete.tags_json != "[]":
        target.tags_json = obsolete.tags_json
    for row in session.exec(
        select(ModelProvenanceField).where(
            ModelProvenanceField.provenance_source_id == obsolete.id
        )
    ).all():
        current = target_fields.get(row.field_name)
        if current is None:
            row.provenance_source_id = target.id
            target_fields[row.field_name] = row
        else:
            if not current.user_override_set and row.user_override_set:
                current.user_value_json = row.user_value_json
                current.user_override_set = True
                current.user_updated_by = row.user_updated_by
                current.user_updated_at = row.user_updated_at
            session.delete(row)
    session.flush()
    captures = {
        row.snapshot_sha256: row
        for row in session.exec(
            select(ProvenanceCapture).where(
                ProvenanceCapture.provenance_source_id == target.id
            )
        ).all()
    }
    for row in session.exec(
        select(ProvenanceCapture).where(
            ProvenanceCapture.provenance_source_id == obsolete.id
        )
    ).all():
        duplicate = captures.get(row.snapshot_sha256)
        if duplicate is None:
            row.provenance_source_id = target.id
            captures[row.snapshot_sha256] = row
        else:
            session.exec(
                ArtifactProvenanceLink.__table__.update()  # type: ignore[attr-defined]
                .where(ArtifactProvenanceLink.capture_id == row.id)
                .values(capture_id=duplicate.id)
            )
            session.delete(row)
    session.exec(
        ArtifactProvenanceLink.__table__.update()  # type: ignore[attr-defined]
        .where(ArtifactProvenanceLink.provenance_source_id == obsolete.id)
        .values(provenance_source_id=target.id)
    )
    session.flush()

    obsolete_cover = session.exec(
        select(ModelSourceCover).where(
            ModelSourceCover.provenance_source_id == obsolete.id
        )
    ).first()
    if obsolete_cover is not None:
        target_cover = session.exec(
            select(ModelSourceCover).where(
                ModelSourceCover.provenance_source_id == target.id
            )
        ).first()
        if target_cover is None:
            # The cover is part of the source's logical provenance, so retain
            # its exact storage key by moving ownership to the survivor.
            obsolete_cover.provenance_source_id = target.id
            session.add(obsolete_cover)
        else:
            # The survivor wins when both sources have covers.  Move the
            # obsolete cover's positive receipt into the DB-first deletion
            # outbox before deleting its row; the caller's transaction keeps
            # source/field/capture/link and storage authorization atomic.
            enqueue_owned_key(
                session,
                get_backend(),
                obsolete_cover.storage_key,
                required_proof=True,
                resource_kind="model_source_cover",
                resource_id=obsolete_cover.id,
            )
            session.delete(obsolete_cover)
    session.flush()
    session.delete(obsolete)
    session.flush()
    return target


def upsert_capture(
    session: Session,
    *,
    model_id: int,
    manifest: CaptureManifestV2,
    inbox_item_id: int | None = None,
    actor_id: int | None = None,
    update_captured_fields: bool = True,
) -> CaptureUpsertResult:
    now = utcnow()
    key = identity_key(manifest)
    provider, source_item_id, canonical_url, source_revision = _source_values(manifest)
    source = _find_source(session, model_id, key)
    legacy_key = _identity_key(provider, canonical_url)
    legacy = (
        _find_source(session, model_id, legacy_key)
        if source_item_id is not None and legacy_key != key
        else None
    )
    if source is not None and legacy is not None and source.id != legacy.id:
        source = _merge_sources(session, source, legacy)
    elif source is None and legacy is not None:
        try:
            with session.begin_nested():
                legacy.identity_key = key
                legacy.source_item_id = source_item_id
                legacy.canonical_url = canonical_url
                legacy.source_revision = source_revision
                legacy.last_checked_at = now
                legacy.updated_at = now
                session.flush()
            source = legacy
        except IntegrityError:
            source = _find_source(session, model_id, key)
            if source is None:
                raise
            stale = _find_source(session, model_id, legacy_key)
            if stale is not None and stale.id != source.id:
                source = _merge_sources(session, source, stale)
    if source is None:
        source = ModelProvenanceSource(
            model_id=model_id,
            provider=provider,
            source_item_id=source_item_id,
            canonical_url=canonical_url,
            identity_key=key,
            source_revision=source_revision,
            tags_json=_canonical_json(list(manifest.source.tags)),
            first_captured_at=now,
            last_checked_at=now,
            created_by=actor_id,
            updated_at=now,
        )
        try:
            with session.begin_nested():
                session.add(source)
                session.flush()
        except IntegrityError:
            source = _find_source(session, model_id, key)
            if source is None:
                raise
    else:
        source.last_checked_at = now
        source.updated_at = now
        source.source_item_id = source_item_id or source.source_item_id
        source.canonical_url = canonical_url
        source.source_revision = source_revision
        source.tags_json = _canonical_json(list(manifest.source.tags))

    assert source.id is not None

    for name, value in manifest.source.fields.items():
        row = session.exec(
            select(ModelProvenanceField).where(
                ModelProvenanceField.provenance_source_id == source.id,
                ModelProvenanceField.field_name == name,
            )
        ).first()
        encoded = _canonical_json(_field_snapshot(value)["value"])
        if row is None:
            candidate = ModelProvenanceField(
                provenance_source_id=source.id,
                field_name=name,
                captured_value_json=encoded,
                captured_origin=value.origin,
                captured_at=now,
            )
            try:
                with session.begin_nested():
                    session.add(candidate)
                    session.flush()
                row = candidate
            except IntegrityError:
                row = session.exec(
                    select(ModelProvenanceField).where(
                        ModelProvenanceField.provenance_source_id == source.id,
                        ModelProvenanceField.field_name == name,
                    )
                ).first()
                if row is None:
                    raise
                if update_captured_fields:
                    row.captured_value_json = encoded
                    row.captured_origin = value.origin
                    row.captured_at = now
        elif update_captured_fields:
            row.captured_value_json = encoded
            row.captured_origin = value.origin
            row.captured_at = now

    # Flush field changes before the capture savepoint.  ``begin_nested``
    # flushes pending work before opening its savepoint, so keeping this
    # explicit makes the unique capture retry isolated rather than rolling
    # back unrelated caller work.
    session.flush()

    digest = snapshot_sha256(manifest)
    capture = session.exec(
        select(ProvenanceCapture).where(
            ProvenanceCapture.provenance_source_id == source.id,
            ProvenanceCapture.snapshot_sha256 == digest,
        )
    ).first()
    if capture is not None:
        capture.checked_at = now
        return CaptureUpsertResult(source, capture, False)
    capture = ProvenanceCapture(
        provenance_source_id=source.id,
        inbox_item_id=inbox_item_id,
        captured_by=actor_id,
        adapter_version=manifest.source.adapter_version,
        source_revision=manifest.source.source_revision,
        snapshot_json=snapshot_json(manifest),
        snapshot_sha256=digest,
        captured_at=now,
        checked_at=now,
    )
    try:
        with session.begin_nested():
            session.add(capture)
            session.flush()
        return CaptureUpsertResult(source, capture, True)
    except IntegrityError:
        capture = session.exec(
            select(ProvenanceCapture).where(
                ProvenanceCapture.provenance_source_id == source.id,
                ProvenanceCapture.snapshot_sha256 == digest,
            )
        ).first()
        if capture is None:
            raise
        capture.checked_at = now
        return CaptureUpsertResult(source, capture, False)


def set_user_override(
    session: Session,
    *,
    provenance_source_id: int,
    field_name: str,
    value: object,
    actor_id: int | None = None,
) -> ModelProvenanceField:
    if field_name not in PROVENANCE_FIELD_NAMES:
        raise ValueError("unsupported_provenance_field")
    row = session.exec(
        select(ModelProvenanceField).where(
            ModelProvenanceField.provenance_source_id == provenance_source_id,
            ModelProvenanceField.field_name == field_name,
        )
    ).one_or_none()
    now = utcnow()
    if row is None:
        # Captures are sparse: a provider may omit an allowlisted field.  A
        # user override still needs a durable row.  The non-null legacy
        # column contract uses an empty JSON string to represent no capture;
        # callers must use captured_at (not this placeholder) to distinguish
        # a captured value from an override-only row.
        candidate = ModelProvenanceField(
            provenance_source_id=provenance_source_id,
            field_name=field_name,
            captured_value_json=_canonical_json(_ABSENT_CAPTURED_VALUE),
            captured_origin=_ABSENT_CAPTURED_ORIGIN,
            captured_at=None,
        )
        try:
            with session.begin_nested():
                session.add(candidate)
                session.flush()
            row = candidate
        except IntegrityError:
            row = session.exec(
                select(ModelProvenanceField).where(
                    ModelProvenanceField.provenance_source_id == provenance_source_id,
                    ModelProvenanceField.field_name == field_name,
                )
            ).one_or_none()
            if row is None:
                raise
    row.user_value_json = _canonical_json(value)
    row.user_override_set = True
    row.user_updated_by = actor_id
    row.user_updated_at = now
    return row


def clear_user_override(
    session: Session,
    *,
    provenance_source_id: int,
    field_name: str,
    actor_id: int | None = None,
) -> ModelProvenanceField | None:
    """Restore the captured value without changing it or committing."""
    if field_name not in PROVENANCE_FIELD_NAMES:
        raise ValueError("unsupported_provenance_field")
    row = session.exec(
        select(ModelProvenanceField).where(
            ModelProvenanceField.provenance_source_id == provenance_source_id,
            ModelProvenanceField.field_name == field_name,
        )
    ).one_or_none()
    if row is None:
        return None
    row.user_value_json = None
    row.user_override_set = False
    row.user_updated_by = actor_id
    row.user_updated_at = utcnow()
    return row


def preflight_existing_artifact(
    session: Session, context: ProvenanceContext
) -> ProvenancePreflightResult:
    """Return a duplicate only after checking the context actor's edit access."""
    blob_sha256 = context.blob_sha256
    if blob_sha256 is None:
        return ProvenancePreflightResult("not_found")
    key = import_key(
        context.manifest,
        source_file_id=context.source_file_id,
        source_filename=context.source_filename,
        blob_sha256=blob_sha256,
    )
    row = session.exec(
        select(ArtifactProvenanceLink, File, Model)
        .join(File, ArtifactProvenanceLink.file_id == File.id)  # type: ignore[arg-type]
        .join(Model, File.model_id == Model.id)  # type: ignore[arg-type]
        .where(ArtifactProvenanceLink.import_key == key)
    ).first()
    if row is None or context.actor_id is None:
        return ProvenancePreflightResult("not_found")
    link, file_row, model = row
    actor = session.get(User, context.actor_id)
    if actor is None or not role_allows(
        effective_collection_role(session, actor, model.collection_id),
        CollectionRole.EDIT,
    ):
        return ProvenancePreflightResult("not_found")
    if model.deleted_at is not None or file_row.deleted_at is not None:
        return ProvenancePreflightResult("trashed", model_id=model.id)
    return ProvenancePreflightResult("reusable", link, model.id, file_row.id)


def attach_ingested_artifact(
    session: Session,
    file_row: File,
    context: ProvenanceContext,
    *,
    update_captured_fields: bool = True,
) -> ArtifactProvenanceLink:
    """Attach capture and link inside the caller's existing ingestion transaction."""
    result = upsert_capture(
        session,
        model_id=file_row.model_id,
        manifest=context.manifest,
        inbox_item_id=context.inbox_item_id,
        actor_id=context.actor_id,
        update_captured_fields=update_captured_fields,
    )
    assert file_row.id is not None and result.source.id is not None
    assert result.capture.id is not None
    if context.blob_sha256 is None:
        raise ValueError("provenance_context_requires_blob_sha256")
    blob_sha256 = _validated_sha256(context.blob_sha256)
    key = import_key(
        context.manifest,
        source_file_id=context.source_file_id,
        source_filename=context.source_filename,
        blob_sha256=blob_sha256,
    )
    existing = session.exec(
        select(ArtifactProvenanceLink).where(ArtifactProvenanceLink.import_key == key)
    ).first()
    if existing is not None:
        if existing.file_id == file_row.id:
            return existing
        raise ValueError("captured_artifact_already_linked") from None
    source_filename = _bounded(context.source_filename, 512, "source_filename")
    assert source_filename is not None
    link = ArtifactProvenanceLink(
        file_id=file_row.id,
        provenance_source_id=result.source.id,
        capture_id=result.capture.id,
        source_file_id=_bounded(context.source_file_id, 255, "source_file_id"),
        source_filename=source_filename,
        container_entry_path=_bounded(
            context.container_entry_path, 1024, "container_entry_path"
        ),
        source_revision=_bounded(
            context.manifest.source.source_revision, 255, "source_revision"
        ),
        blob_sha256=blob_sha256,
        import_key=key,
    )
    try:
        with session.begin_nested():
            session.add(link)
            session.flush()
        return link
    except IntegrityError:
        existing = session.exec(
            select(ArtifactProvenanceLink).where(
                ArtifactProvenanceLink.import_key == key
            )
        ).first()
        if existing is None:
            raise
        if existing.file_id == file_row.id:
            return existing
        raise ValueError("captured_artifact_already_linked") from None


def attach_existing_artifact(
    session: Session,
    file_row: File,
    context: ProvenanceContext,
    *,
    imported_overrides: Mapping[str, object] | None = None,
) -> PortableProvenanceMergeResult:
    """Merge portable provenance onto an existing Artifact without blob I/O or commit."""
    link = attach_ingested_artifact(
        session, file_row, context, update_captured_fields=False
    )
    imported: list[str] = []
    conflicts: list[str] = []
    for field_name, value in (imported_overrides or {}).items():
        if field_name not in PROVENANCE_FIELD_NAMES:
            raise ValueError("unsupported_provenance_field")
        row = session.exec(
            select(ModelProvenanceField).where(
                ModelProvenanceField.provenance_source_id == link.provenance_source_id,
                ModelProvenanceField.field_name == field_name,
            )
        ).one_or_none()
        if row is None:
            # Sparse captures intentionally have no captured field row.  A
            # portable override still has to survive the merge, so create the
            # durable override row through the same public seam as local
            # edits.  Its captured_at remains null and is never exported as a
            # captured value.
            set_user_override(
                session,
                provenance_source_id=link.provenance_source_id,
                field_name=field_name,
                value=value,
                actor_id=context.actor_id,
            )
            imported.append(field_name)
            continue
        encoded = _canonical_json(value)
        if row.user_override_set:
            if row.user_value_json != encoded:
                conflicts.append(field_name)
            continue
        row.user_value_json = encoded
        row.user_override_set = True
        row.user_updated_by = context.actor_id
        row.user_updated_at = utcnow()
        imported.append(field_name)
    return PortableProvenanceMergeResult(link, tuple(imported), tuple(conflicts))
