"""Public share-link service.

The trust boundary: a share token is a bearer capability for *one* model. Every
public request funnels through :func:`resolve_share`, which returns the link
only when it exists, is not revoked, and has not expired — and raises a uniform
404 otherwise so tokens/models cannot be probed for existence. File access is
always re-scoped to ``share.model_id`` by the caller.
"""

from __future__ import annotations

import secrets
import json
from datetime import timedelta

from fastapi import HTTPException
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import File, FileType, Metadata, Model, ShareLink
from app.db.scopes import live
from app.services.auth import _as_utc, _token_hash
from app.schemas.share import PublicFileRead, PublicModelRead, ShareLinkRead

_TOKEN_BYTES = 32


def _selected_file_ids(link: ShareLink) -> list[int] | None:
    if not link.selected_file_ids_json:
        return None
    try:
        parsed = json.loads(link.selected_file_ids_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    return [int(item) for item in parsed if isinstance(item, int)]


def is_active(link: ShareLink) -> bool:
    # expires_at comes back naive from SQLite; normalise before comparing.
    return link.revoked_at is None and _as_utc(link.expires_at) > utcnow()


def to_read(link: ShareLink) -> ShareLinkRead:
    return ShareLinkRead(
        id=link.id,  # type: ignore[arg-type]
        model_id=link.model_id,
        expires_at=link.expires_at,
        revoked_at=link.revoked_at,
        allow_download=link.allow_download,
        revision_file_ids=_selected_file_ids(link),
        access_count=link.access_count,
        created_at=link.created_at,
        is_active=is_active(link),
    )


def create_share(
    session: Session,
    *,
    model_id: int,
    expires_in_days: int,
    allow_download: bool,
    revision_file_ids: list[int] | None = None,
    created_by: int | None,
) -> tuple[ShareLink, str]:
    """Create a share link, returning (row, raw_token). Only the hash is stored."""
    raw_token = secrets.token_urlsafe(_TOKEN_BYTES)
    days = max(1, min(expires_in_days, 365))
    selected_ids: list[int] | None = None
    if revision_file_ids is not None:
        deduped_ids = list(dict.fromkeys(revision_file_ids))
        if not deduped_ids:
            raise HTTPException(status_code=400, detail="no_revisions_selected")
        selected = session.exec(
            select(File.id)
            .where(File.model_id == model_id)
            .where(File.file_type == FileType.GCODE)
            .where(live(File))
            .where(File.id.in_(deduped_ids))  # type: ignore[attr-defined]
        ).all()
        selected_ids = [int(file_id) for file_id in selected]
        if len(selected_ids) != len(deduped_ids):
            raise HTTPException(status_code=400, detail="invalid_revision_file_id")
    link = ShareLink(
        model_id=model_id,
        token_hash=_token_hash(raw_token),
        expires_at=utcnow() + timedelta(days=days),
        allow_download=allow_download,
        selected_file_ids_json=json.dumps(selected_ids) if selected_ids else None,
        created_by=created_by,
    )
    session.add(link)
    session.commit()
    session.refresh(link)
    return link, raw_token


def list_shares(session: Session, model_id: int) -> list[ShareLink]:
    # Revoked links are hidden from the management list. The row is kept (not
    # hard-deleted) so the token stays permanently invalid — the public lookup
    # still finds it, sees it's revoked, and returns the uniform 404.
    return list(
        session.exec(
            select(ShareLink)
            .where(
                ShareLink.model_id == model_id,
                ShareLink.revoked_at.is_(None),  # type: ignore[union-attr]
            )
            .order_by(ShareLink.created_at.desc())  # type: ignore[attr-defined]
        ).all()
    )


def revoke_share(session: Session, link: ShareLink) -> ShareLink:
    if link.revoked_at is None:
        link.revoked_at = utcnow()
        session.add(link)
        session.commit()
        session.refresh(link)
    return link


def resolve_share(session: Session, token: str) -> ShareLink:
    """Return the active link for *token*, or raise a uniform 404.

    The model must also still be live; otherwise the link is dead. All failure
    modes collapse to 404 to avoid leaking which tokens/models exist.
    """
    if not token:
        raise HTTPException(status_code=404, detail="not_found")
    link = session.exec(
        select(ShareLink).where(ShareLink.token_hash == _token_hash(token))
    ).first()
    if link is None or not is_active(link):
        raise HTTPException(status_code=404, detail="not_found")
    model = session.get(Model, link.model_id)
    if model is None or model.deleted_at is not None:
        raise HTTPException(status_code=404, detail="not_found")
    return link


def record_access(session: Session, link: ShareLink) -> None:
    link.access_count += 1
    session.add(link)
    session.commit()


def public_detail(session: Session, link: ShareLink) -> PublicModelRead:
    """Build the read-only projection for the model behind *link*."""
    model = session.get(Model, link.model_id)
    assert model is not None  # resolve_share already validated liveness
    selected_ids = _selected_file_ids(link)
    gcode_revision_numbers = _gcode_revision_numbers(session, link.model_id)
    rows = session.exec(
        select(File, Metadata)
        .where(File.model_id == link.model_id)
        .where(live(File))
        .outerjoin(Metadata, Metadata.file_id == File.id)
        .order_by(File.version.asc())  # type: ignore[attr-defined]
    ).all()
    files = [
        PublicFileRead(
            id=f.id,  # type: ignore[arg-type]
            original_filename=f.original_filename,
            file_type=f.file_type.value,
            size_bytes=f.size_bytes,
            version=f.version,
            gcode_revision_number=gcode_revision_numbers.get(f.id),
            revision_label=f.revision_label,
            revision_status=f.revision_status,
            revision_notes=f.revision_notes,
            is_recommended=f.is_recommended,
            bbox_x_mm=md.bbox_x_mm if md else None,
            bbox_y_mm=md.bbox_y_mm if md else None,
            bbox_z_mm=md.bbox_z_mm if md else None,
            triangle_count=md.triangle_count if md else None,
            slicer_name=md.slicer_name if md else None,
            slicer_version=md.slicer_version if md else None,
            printer_model=md.printer_model if md else None,
            nozzle_diameter_mm=md.nozzle_diameter_mm if md else None,
            layer_height_mm=md.layer_height_mm if md else None,
            first_layer_height_mm=md.first_layer_height_mm if md else None,
            infill_percent=md.infill_percent if md else None,
            wall_loops=md.wall_loops if md else None,
            support_material=md.support_material if md else None,
            nozzle_temperature_c=md.nozzle_temperature_c if md else None,
            bed_temperature_c=md.bed_temperature_c if md else None,
            estimated_time_s=md.estimated_time_s if md else None,
            filament_weight_g=md.filament_weight_g if md else None,
            filament_length_mm=md.filament_length_mm if md else None,
            filament_cost=md.filament_cost if md else None,
            material_type=md.material_type if md else None,
            material_brand=md.material_brand if md else None,
        )
        for f, md in rows
        if _file_allowed(link, f, selected_ids)
    ]
    return PublicModelRead(
        name=model.name,
        description=model.description,
        has_thumbnail=bool(model.thumbnail_file_id or model.thumbnail_path),
        allow_download=link.allow_download,
        files=files,
    )


def share_file_or_404(session: Session, link: ShareLink, file_id: int) -> File:
    """Return *file_id* only if it belongs to the shared model — else 404.

    This is the isolation check that stops a share holder from enumerating
    file ids across the rest of the vault.
    """
    f = session.get(File, file_id)
    if (
        f is None
        or f.deleted_at is not None
        or f.model_id != link.model_id
        or not _file_allowed(link, f)
    ):
        raise HTTPException(status_code=404, detail="not_found")
    return f


def _file_allowed(
    link: ShareLink, file_row: File, selected_ids: list[int] | None = None
) -> bool:
    selected_ids = selected_ids if selected_ids is not None else _selected_file_ids(link)
    if selected_ids is None:
        return True
    return file_row.file_type != FileType.GCODE or file_row.id in selected_ids


def _gcode_revision_numbers(session: Session, model_id: int) -> dict[int, int]:
    rows = session.exec(
        select(File)
        .where(File.model_id == model_id)
        .where(File.file_type == FileType.GCODE)
        .where(live(File))
        .order_by(File.version.asc())  # type: ignore[attr-defined]
    ).all()
    return {f.id: i for i, f in enumerate(rows, start=1) if f.id is not None}
