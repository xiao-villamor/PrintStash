"""Local printer preset catalog detected from slicer metadata."""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session, select

from app.core.http import get_or_404
from app.core.security import require_auth
from app.core.time import utcnow
from app.db.models import PrinterProfile
from app.db.session import get_session
from app.schemas.models import (
    PrinterProfileCreate,
    PrinterProfileRead,
    PrinterProfileUpdate,
)

router = APIRouter(prefix="/printer-profiles", tags=["printer-profiles"])


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _read(profile: PrinterProfile) -> PrinterProfileRead:
    return PrinterProfileRead(**profile.model_dump())


@router.get(
    "",
    response_model=List[PrinterProfileRead],
    summary="List local printer presets",
)
def list_printer_profiles(
    session: Session = Depends(get_session),
) -> List[PrinterProfileRead]:
    profiles = session.exec(
        select(PrinterProfile).order_by(PrinterProfile.name.asc())  # type: ignore[attr-defined]
    ).all()
    return [_read(profile) for profile in profiles]


@router.post(
    "",
    response_model=PrinterProfileRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
    summary="Create a local printer preset",
)
def create_printer_profile(
    payload: PrinterProfileCreate,
    session: Session = Depends(get_session),
) -> PrinterProfileRead:
    name = payload.name.strip()
    existing = session.exec(
        select(PrinterProfile).where(PrinterProfile.name == name)
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="printer_profile_already_exists")

    profile = PrinterProfile(
        name=name,
        printer_model=_clean(payload.printer_model),
        slicer_name=_clean(payload.slicer_name),
        nozzle_diameter_mm=payload.nozzle_diameter_mm,
        notes=_clean(payload.notes),
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return _read(profile)


@router.patch(
    "/{profile_id}",
    response_model=PrinterProfileRead,
    dependencies=[Depends(require_auth)],
    summary="Update a local printer preset",
)
def update_printer_profile(
    profile_id: int,
    payload: PrinterProfileUpdate,
    session: Session = Depends(get_session),
) -> PrinterProfileRead:
    profile = get_or_404(
        session, PrinterProfile, profile_id, "printer_profile_not_found"
    )

    if payload.name is not None:
        name = payload.name.strip()
        existing = session.exec(
            select(PrinterProfile).where(
                PrinterProfile.name == name,
                PrinterProfile.id != profile_id,
            )
        ).first()
        if existing:
            raise HTTPException(
                status_code=409, detail="printer_profile_already_exists"
            )
        profile.name = name

    fields_set = payload.model_fields_set
    if "printer_model" in fields_set:
        profile.printer_model = _clean(payload.printer_model)
    if "slicer_name" in fields_set:
        profile.slicer_name = _clean(payload.slicer_name)
    if "nozzle_diameter_mm" in fields_set:
        profile.nozzle_diameter_mm = payload.nozzle_diameter_mm
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
    summary="Delete a local printer preset",
)
def delete_printer_profile(
    profile_id: int,
    session: Session = Depends(get_session),
) -> Response:
    profile = get_or_404(
        session, PrinterProfile, profile_id, "printer_profile_not_found"
    )
    session.delete(profile)
    session.commit()
    return Response(status_code=204)
