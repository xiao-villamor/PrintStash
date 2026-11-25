"""Filament profile selection and estimated cost calculation for print history."""

from __future__ import annotations

from collections import defaultdict

from sqlmodel import Session, select

from app.db.models import (
    FilamentProfile,
    File,
    Metadata,
)
from app.db.scopes import live
from app.schemas.plates import GcodePlateMetadata


def _normalise_profile_key(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip().lower()
    return stripped or None


def cost_profiles(session: Session) -> list[FilamentProfile]:
    """All filament profiles, loaded once per read-model composition.

    The table holds a handful of presets; matching in memory avoids the
    per-Metadata lookup queries that turned detail/export into N+1.
    """
    return list(session.exec(select(FilamentProfile)).all())


def filament_cost_for_grams(
    profiles: list[FilamentProfile],
    metadata: Metadata | None,
    grams: float | None,
) -> float | None:
    """Cost of *grams* of filament using the profile matching *metadata*.

    Used for measured per-print cost (PrintJob.filament_used_g). Returns None
    when grams, a matching profile, or its cost_per_kg is missing.
    """
    if grams is None or metadata is None:
        return None
    profile = match_cost_profile(profiles, metadata)
    if profile is None or profile.cost_per_kg is None:
        return None
    return round(grams * profile.cost_per_kg / 1000, 4)


def filament_cost_for_job(
    profiles: list[FilamentProfile],
    metadata: Metadata | None,
    grams: float | None,
    spool_filament_id: int | None,
) -> float | None:
    """Per-print cost, preferring the exact synced spool over metadata matching.

    When a Spoolman spool was selected, its synced FilamentProfile gives the
    exact cost; otherwise fall back to the fuzzy metadata match.
    """
    if grams is None:
        return None
    rate = filament_rate_for_job(profiles, metadata, spool_filament_id)
    return round(grams * rate / 1000, 4) if rate is not None else None


def filament_rate_for_job(
    profiles: list[FilamentProfile],
    metadata: Metadata | GcodePlateMetadata | None,
    spool_filament_id: int | None,
) -> float | None:
    if spool_filament_id is not None:
        for profile in profiles:
            if (
                profile.spoolman_filament_id == spool_filament_id
                and profile.cost_per_kg is not None
            ):
                return profile.cost_per_kg
    profile = match_cost_profile(profiles, metadata) if metadata is not None else None
    return profile.cost_per_kg if profile is not None else None


def _matching_filament_profile_by_fields(
    profiles: list[FilamentProfile],
    material_brand: str | None,
    material_type: str | None,
) -> FilamentProfile | None:
    candidates = [
        _normalise_profile_key(material_brand),
        _normalise_profile_key(material_type),
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        for profile in profiles:
            if _normalise_profile_key(profile.name) == candidate:
                return profile

    norm_type = _normalise_profile_key(material_type)
    norm_brand = _normalise_profile_key(material_brand)
    if norm_type is None:
        return None

    for profile in profiles:
        if _normalise_profile_key(profile.material_type) != norm_type:
            continue
        if norm_brand is not None:
            if _normalise_profile_key(profile.material_brand) == norm_brand:
                return profile
        elif profile.material_brand is None:
            return profile
    return None


def match_cost_profile(
    profiles: list[FilamentProfile],
    metadata: Metadata | GcodePlateMetadata,
) -> FilamentProfile | None:
    return _matching_filament_profile_by_fields(
        profiles, metadata.material_brand, metadata.material_type
    )


def filament_profile_usage(session: Session) -> dict[int, int]:
    """Live-file count per filament profile id, using the same brand/type
    matching that drives cost estimates.

    Selects only the two matched columns instead of hydrating full Metadata
    rows — this scans every live file's metadata, so the row stays as thin as
    the matching logic allows.
    """
    profiles = cost_profiles(session)
    counts: dict[int, int] = defaultdict(int)
    rows = session.exec(
        select(Metadata.material_brand, Metadata.material_type)
        .join(File, File.id == Metadata.file_id)
        .where(live(File))
    ).all()
    for material_brand, material_type in rows:
        profile = _matching_filament_profile_by_fields(
            profiles, material_brand, material_type
        )
        if profile is not None and profile.id is not None:
            counts[profile.id] += 1
    return dict(counts)


def printer_profile_usage(session: Session) -> dict[int, int]:
    """Live-file count per printer profile id, matched on preset name or
    bare printer model. Selects only the matched column (see
    ``filament_profile_usage``)."""
    from app.db.models import PrinterProfile

    profiles = list(session.exec(select(PrinterProfile)).all())
    counts: dict[int, int] = defaultdict(int)
    rows = session.exec(
        select(Metadata.printer_model)
        .join(File, File.id == Metadata.file_id)
        .where(live(File))
    ).all()
    for printer_model in rows:
        key = _normalise_profile_key(printer_model)
        if key is None:
            continue
        for profile in profiles:
            if key in (
                _normalise_profile_key(profile.name),
                _normalise_profile_key(profile.printer_model),
            ):
                if profile.id is not None:
                    counts[profile.id] += 1
                break
    return dict(counts)
