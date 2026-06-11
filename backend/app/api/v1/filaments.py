"""Local filament preset catalog used for cost estimates."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session, select

from app.core.http import get_or_404
from app.core.security import require_auth
from app.core.time import utcnow
from app.db.models import FilamentProfile
from app.db.session import get_session
from app.schemas.models import (
    FilamentProfileCreate,
    FilamentProfileRead,
    FilamentProfileUpdate,
)
from app.services import model_views

router = APIRouter(prefix="/filament-profiles", tags=["filament-profiles"])


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _read(profile: FilamentProfile, usage_count: int = 0) -> FilamentProfileRead:
    return FilamentProfileRead(**profile.model_dump(), usage_count=usage_count)


@router.get(
    "",
    response_model=List[FilamentProfileRead],
    summary="List local filament presets",
)
def list_filament_profiles(
    session: Session = Depends(get_session),
) -> List[FilamentProfileRead]:
    profiles = session.exec(
        select(FilamentProfile).order_by(FilamentProfile.name.asc())  # type: ignore[attr-defined]
    ).all()
    usage = model_views.filament_profile_usage(session)
    return [_read(profile, usage.get(profile.id or 0, 0)) for profile in profiles]


@router.post(
    "",
    response_model=FilamentProfileRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
    summary="Create a local filament preset",
)
def create_filament_profile(
    payload: FilamentProfileCreate,
    session: Session = Depends(get_session),
) -> FilamentProfileRead:
    name = payload.name.strip()
    existing = session.exec(
        select(FilamentProfile).where(FilamentProfile.name == name)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="filament_profile_already_exists")

    profile = FilamentProfile(
        name=name,
        material_type=_clean(payload.material_type),
        material_brand=_clean(payload.material_brand),
        cost_per_kg=payload.cost_per_kg,
        notes=_clean(payload.notes),
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return _read(profile)


@router.patch(
    "/{profile_id}",
    response_model=FilamentProfileRead,
    dependencies=[Depends(require_auth)],
    summary="Update a local filament preset",
)
def update_filament_profile(
    profile_id: int,
    payload: FilamentProfileUpdate,
    session: Session = Depends(get_session),
) -> FilamentProfileRead:
    profile = get_or_404(
        session, FilamentProfile, profile_id, "filament_profile_not_found"
    )

    if payload.name is not None:
        name = payload.name.strip()
        existing = session.exec(
            select(FilamentProfile).where(
                FilamentProfile.name == name,
                FilamentProfile.id != profile_id,
            )
        ).first()
        if existing:
            raise HTTPException(
                status_code=409, detail="filament_profile_already_exists"
            )
        profile.name = name

    fields_set = payload.model_fields_set
    if "material_type" in fields_set:
        profile.material_type = _clean(payload.material_type)
    if "material_brand" in fields_set:
        profile.material_brand = _clean(payload.material_brand)
    if "cost_per_kg" in fields_set:
        profile.cost_per_kg = payload.cost_per_kg
    if "notes" in fields_set:
        profile.notes = _clean(payload.notes)

    profile.updated_at = utcnow()
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return _read(profile)


@router.delete(
    "/{profile_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    dependencies=[Depends(require_auth)],
    summary="Delete a local filament preset",
)
def delete_filament_profile(
    profile_id: int,
    session: Session = Depends(get_session),
) -> Response:
    profile = get_or_404(
        session, FilamentProfile, profile_id, "filament_profile_not_found"
    )
    session.delete(profile)
    session.commit()
    return Response(status_code=204)
