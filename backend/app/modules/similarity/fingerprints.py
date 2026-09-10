"""Input-versioned fingerprints with shared leases and atomic publication."""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, or_, select

from app.core.time import utcnow
from app.db.models import ExternalLibraryTombstone, File, GeometryFingerprint, Model
from app.db.scopes import live
from app.modules.media.fingerprints import ALGORITHM_VERSION, FingerprintResult

LEASE_SECONDS = 900
JSON_LIMIT = 2 * 1024 * 1024


def live_source_predicates(file=File, model=Model):
    tombstone = (
        select(ExternalLibraryTombstone.id)
        .where(
            ExternalLibraryTombstone.library_id == file.external_library_id,
            ExternalLibraryTombstone.source_key == file.source_key,
            col(ExternalLibraryTombstone.cleared_at).is_(None),
        )
        .correlate(file)
        .exists()
    )
    return (
        live(file),
        live(model),
        col(file.purge_token).is_(None),
        col(model.purge_token).is_(None),
        ~tombstone,
    )


def current_source(session: Session, file_id: int, source_hash: str) -> bool:
    return (
        session.exec(
            select(File.id)
            .join(Model, Model.id == File.model_id)
            .where(
                File.id == file_id,
                File.sha256 == source_hash,
                *live_source_predicates(),
            )
        ).first()
        is not None
    )


def _row(
    session: Session, file: File, component_index: int, algorithm_version: str
) -> GeometryFingerprint:
    query = select(GeometryFingerprint).where(
        GeometryFingerprint.file_id == file.id,
        GeometryFingerprint.source_sha256 == file.sha256,
        GeometryFingerprint.component_index == component_index,
        GeometryFingerprint.algorithm_version == algorithm_version,
    )
    existing = session.exec(query).first()
    if existing is not None:
        return existing
    try:
        with session.begin_nested():
            created = GeometryFingerprint(
                file_id=file.id,
                source_sha256=file.sha256,
                component_index=component_index,
                algorithm_version=algorithm_version,
            )
            session.add(created)
            session.flush()
        return created
    except IntegrityError:
        return session.exec(query).one()


def claim(
    session: Session, file: File, *, retry_incomplete: bool = False
) -> tuple[int, str] | None:
    if file.id is None or not current_source(session, file.id, file.sha256):
        return None
    row = _row(session, file, 0, ALGORITHM_VERSION)
    assert row.id is not None
    token = secrets.token_hex(32)
    now = utcnow()
    states = (
        ["pending", "failed", "partial", "unsupported"]
        if retry_incomplete
        else ["pending"]
    )
    changed = session.connection().execute(
        update(GeometryFingerprint)
        .where(
            GeometryFingerprint.id == row.id,
            col(GeometryFingerprint.state).in_(states),
            or_(
                col(GeometryFingerprint.lease_token).is_(None),
                col(GeometryFingerprint.lease_expires_at) <= now,
            ),
        )
        .values(
            state="pending",
            lease_token=token,
            lease_expires_at=now + timedelta(seconds=LEASE_SECONDS),
            attempts=col(GeometryFingerprint.attempts) + 1,
            updated_at=now,
        )
    )
    session.commit()
    return (row.id, token) if changed.rowcount == 1 else None


def release(session: Session, fingerprint_id: int, token: str) -> None:
    session.connection().execute(
        update(GeometryFingerprint)
        .where(
            GeometryFingerprint.id == fingerprint_id,
            GeometryFingerprint.lease_token == token,
        )
        .values(lease_token=None, lease_expires_at=None, updated_at=utcnow())
    )
    session.commit()


def publish(
    session: Session,
    file: File,
    result: FingerprintResult,
    *,
    fingerprint_id: int,
    token: str,
    duration_ms: int | None = None,
    peak_rss_bytes: int | None = None,
) -> bool:
    """A lease and current source must still agree in the publishing transaction."""
    if file.id is None:
        return False
    now = utcnow()
    source = (
        select(File.id)
        .join(Model, Model.id == File.model_id)
        .where(
            File.id == file.id,
            File.sha256 == file.sha256,
            *live_source_predicates(),
        )
        .exists()
    )
    fenced = session.connection().execute(
        update(GeometryFingerprint)
        .where(
            GeometryFingerprint.id == fingerprint_id,
            GeometryFingerprint.file_id == file.id,
            GeometryFingerprint.source_sha256 == file.sha256,
            GeometryFingerprint.algorithm_version == result.algorithm_version,
            GeometryFingerprint.lease_token == token,
            col(GeometryFingerprint.lease_expires_at) > now,
            source,
        )
        .values(
            lease_token=None,
            lease_expires_at=None,
            state=result.state,
            failure_code=result.failure_code,
            duration_ms=duration_ms,
            peak_rss_bytes=peak_rss_bytes,
            updated_at=now,
        )
    )
    if fenced.rowcount != 1:
        session.rollback()
        return False
    try:
        for record in result.records:
            row = _row(session, file, record.component_index, result.algorithm_version)
            session.refresh(row)
            values = record.values
            for key in (
                "vertex_count",
                "face_count",
                "component_count",
                "euler_characteristic",
                "watertight",
                "surface_area",
                "volume",
                "area_volume_ratio",
                "normalized_area",
                "hull_ratio",
                "fill_ratio",
                "eigen_ratio_0",
                "inertia_ratio_0",
                "inertia_ratio_1",
                "radius",
                "d2_blob",
                "sh_blob",
                "view_blob",
            ):
                setattr(row, key, values.get(key))
            keys = values["keys"]
            for group in ("physical", "normalized"):
                for index in range(4):
                    setattr(
                        row,
                        f"{group}_hash_{index}",
                        keys[group][index] if keys is not None else None,
                    )
            row.instance_count = record.instance_count
            row.recipe_json = encode_json(values["recipe"])
            row.unavailable_json = encode_json(values["unavailable"])
            row.instances_json = encode_json(record.instances)
            row.metrics_json = encode_json(
                {
                    key: value
                    for key, value in values.items()
                    if not key.endswith("_blob")
                    and key not in ("recipe", "unavailable", "keys")
                }
            )
            row.state = result.state
            row.failure_code = result.failure_code
            row.updated_at = now
            session.add(row)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return True


def publish_precomputed(session: Session, file: File, result: FingerprintResult) -> str:
    if file.id is None or not current_source(session, file.id, file.sha256):
        return "stale"
    claimed = claim(session, file)
    if claimed is None:
        row = _row(session, file, 0, ALGORITHM_VERSION)
        return row.state
    return (
        result.state
        if publish(session, file, result, fingerprint_id=claimed[0], token=claimed[1])
        else "stale"
    )


def source_digest(path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def encode_json(value: object) -> str:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True, allow_nan=False)
    if len(encoded.encode()) > JSON_LIMIT:
        raise ValueError("similarity_metadata_limit")
    return encoded
