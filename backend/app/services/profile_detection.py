"""Create local profiles from parsed slicer metadata."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import FilamentProfile, PrinterProfile


def _clean(value: object) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def _to_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _infer_cost_per_kg(meta: dict[str, Any]) -> float | None:
    filament_cost = _to_float(meta.get("filament_cost"))
    filament_weight_g = _to_float(meta.get("filament_weight_g"))
    if filament_cost is None or filament_weight_g is None or filament_weight_g <= 0:
        return None
    return round(filament_cost / filament_weight_g * 1000, 4)


def _find_filament_profile(
    session: Session,
    *,
    name: str,
    material_type: str | None,
    material_brand: str | None,
) -> FilamentProfile | None:
    profile = session.exec(
        select(FilamentProfile).where(func.lower(FilamentProfile.name) == name.lower())
    ).first()
    if profile is not None:
        return profile

    if material_type is None:
        return None

    stmt = select(FilamentProfile).where(
        func.lower(FilamentProfile.material_type) == material_type.lower()
    )
    if material_brand is not None:
        stmt = stmt.where(
            func.lower(FilamentProfile.material_brand) == material_brand.lower()
        )
    else:
        stmt = stmt.where(FilamentProfile.material_brand.is_(None))
    return session.exec(stmt).first()


def upsert_detected_filament_profile(
    session: Session,
    meta: dict[str, Any],
) -> FilamentProfile | None:
    material_type = _clean(meta.get("material_type"))
    material_brand = _clean(meta.get("material_brand"))
    name = material_brand or material_type
    if name is None:
        return None

    inferred_cost_per_kg = _infer_cost_per_kg(meta)
    profile = _find_filament_profile(
        session,
        name=name,
        material_type=material_type,
        material_brand=material_brand,
    )

    if profile is None:
        profile = FilamentProfile(
            name=name,
            material_type=material_type,
            material_brand=material_brand,
            cost_per_kg=inferred_cost_per_kg,
            notes=(
                "Cost/kg inferred from slicer total filament cost."
                if inferred_cost_per_kg is not None
                else None
            ),
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return profile

    changed = False
    if profile.material_type is None and material_type is not None:
        profile.material_type = material_type
        changed = True
    if profile.material_brand is None and material_brand is not None:
        profile.material_brand = material_brand
        changed = True
    if profile.cost_per_kg is None and inferred_cost_per_kg is not None:
        profile.cost_per_kg = inferred_cost_per_kg
        if profile.notes is None:
            profile.notes = "Cost/kg inferred from slicer total filament cost."
        changed = True

    if changed:
        profile.updated_at = utcnow()
        session.add(profile)
        session.commit()
        session.refresh(profile)
    return profile


def _find_printer_profile(
    session: Session,
    *,
    name: str,
    printer_model: str,
) -> PrinterProfile | None:
    profile = session.exec(
        select(PrinterProfile).where(func.lower(PrinterProfile.name) == name.lower())
    ).first()
    if profile is not None:
        return profile
    return session.exec(
        select(PrinterProfile).where(
            func.lower(PrinterProfile.printer_model) == printer_model.lower()
        )
    ).first()


def upsert_detected_printer_profile(
    session: Session,
    meta: dict[str, Any],
) -> PrinterProfile | None:
    printer_model = _clean(meta.get("printer_model"))
    if printer_model is None:
        return None

    slicer_name = _clean(meta.get("slicer_name"))
    nozzle_diameter_mm = _to_float(meta.get("nozzle_diameter_mm"))
    profile = _find_printer_profile(
        session,
        name=printer_model,
        printer_model=printer_model,
    )

    if profile is None:
        profile = PrinterProfile(
            name=printer_model,
            printer_model=printer_model,
            slicer_name=slicer_name,
            nozzle_diameter_mm=nozzle_diameter_mm,
        )
        session.add(profile)
        session.commit()
        session.refresh(profile)
        return profile

    changed = False
    if profile.printer_model is None:
        profile.printer_model = printer_model
        changed = True
    if profile.slicer_name is None and slicer_name is not None:
        profile.slicer_name = slicer_name
        changed = True
    if profile.nozzle_diameter_mm is None and nozzle_diameter_mm is not None:
        profile.nozzle_diameter_mm = nozzle_diameter_mm
        changed = True

    if changed:
        profile.updated_at = utcnow()
        session.add(profile)
        session.commit()
        session.refresh(profile)
    return profile


def upsert_detected_profiles(session: Session, meta: dict[str, Any]) -> None:
    upsert_detected_filament_profile(session, meta)
    upsert_detected_printer_profile(session, meta)
