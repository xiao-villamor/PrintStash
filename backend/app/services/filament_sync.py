"""Sync Spoolman filaments into local FilamentProfile presets.

One-directional, Spoolman → PrintStash: Spoolman is the source of truth for
filament *types* (material, vendor, price, density, diameter), so a synced
``FilamentProfile`` is a read-only mirror keyed by ``spoolman_filament_id``.
This removes the "same filament maintained in two apps" drift the integration
otherwise invites, while local-only presets (no link) stay editable for installs
that don't run Spoolman.

Mapping (Spoolman ``filament`` → ``FilamentProfile``):
- ``name``            → ``name``
- ``material``        → ``material_type``
- ``vendor.name``     → ``material_brand``
- ``price``/``weight``→ ``cost_per_kg`` (price is per full spool of net ``weight`` g)
- ``density``         → ``density_g_cm3``
- ``diameter``        → ``diameter_mm``
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from sqlmodel import Session, select

from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import FilamentProfile
from app.services import runtime_config
from app.services.spoolman import SpoolmanError, get_spoolman_client

logger = get_logger(__name__)


@dataclass
class SyncResult:
    created: int = 0
    updated: int = 0
    adopted: int = 0
    unlinked: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "created": self.created,
            "updated": self.updated,
            "adopted": self.adopted,
            "unlinked": self.unlinked,
        }


def cost_per_kg_from_filament(filament: dict[str, Any]) -> Optional[float]:
    """Derive $/kg from Spoolman's per-spool ``price`` and net ``weight`` (g)."""
    price = filament.get("price")
    weight = filament.get("weight")
    if not price or not weight or weight <= 0:
        return None
    return round(float(price) / (float(weight) / 1000.0), 4)


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def _unique_name(session: Session, base: str, exclude_id: Optional[int]) -> str:
    """Return ``base`` or a ``base (2)`` style suffix so the unique name holds."""
    name = base
    suffix = 2
    while True:
        clash = session.exec(
            select(FilamentProfile).where(FilamentProfile.name == name)
        ).first()
        if clash is None or clash.id == exclude_id:
            return name
        name = f"{base} ({suffix})"
        suffix += 1


async def sync_from_spoolman(session: Session) -> SyncResult:
    """Pull Spoolman filaments and reconcile local presets.

    Raises :class:`SpoolmanError` when disabled or unreachable so the caller can
    surface a clear message; never partially commits a single filament.
    """
    if not runtime_config.spoolman_enabled(session):
        raise SpoolmanError("Spoolman is not enabled", code="disabled")

    client = get_spoolman_client(session)
    filaments = await client.list_filaments()

    result = SyncResult()
    seen_ids: set[int] = set()

    for filament in filaments:
        fid = filament.get("id")
        if fid is None:
            continue
        fid = int(fid)
        seen_ids.add(fid)

        vendor = filament.get("vendor") or {}
        name = (filament.get("name") or "").strip() or f"Spoolman filament {fid}"
        material = (filament.get("material") or "").strip() or None
        brand = (vendor.get("name") or "").strip() or None
        cost = cost_per_kg_from_filament(filament)
        density = filament.get("density")
        diameter = filament.get("diameter")

        profile = session.exec(
            select(FilamentProfile).where(
                FilamentProfile.spoolman_filament_id == fid
            )
        ).first()

        adopted = False
        if profile is None:
            # First sync: adopt an existing local preset that clearly refers to
            # the same filament (name + material) rather than creating a dup.
            for candidate in session.exec(
                select(FilamentProfile).where(
                    FilamentProfile.spoolman_filament_id.is_(None)  # type: ignore[attr-defined]
                )
            ).all():
                if _norm(candidate.name) == _norm(name) and _norm(
                    candidate.material_type
                ) == _norm(material):
                    profile = candidate
                    adopted = True
                    break

        if profile is None:
            profile = FilamentProfile(name=_unique_name(session, name, None))
            session.add(profile)
            result.created += 1
        else:
            # Keep the unique-name invariant if Spoolman renamed the filament.
            if _norm(profile.name) != _norm(name):
                profile.name = _unique_name(session, name, profile.id)
            if adopted:
                result.adopted += 1
            else:
                result.updated += 1

        profile.spoolman_filament_id = fid
        profile.material_type = material
        profile.material_brand = brand
        if cost is not None:
            profile.cost_per_kg = cost
        profile.density_g_cm3 = float(density) if density else None
        profile.diameter_mm = float(diameter) if diameter else None
        profile.updated_at = utcnow()
        session.add(profile)

    # Filaments removed in Spoolman: unlink (revert to editable local), never
    # delete — the preset may still back historical prints and cost figures.
    for orphan in session.exec(
        select(FilamentProfile).where(
            FilamentProfile.spoolman_filament_id.is_not(None)  # type: ignore[attr-defined]
        )
    ).all():
        if orphan.spoolman_filament_id not in seen_ids:
            orphan.spoolman_filament_id = None
            orphan.updated_at = utcnow()
            session.add(orphan)
            result.unlinked += 1

    session.commit()
    logger.info("Spoolman filament sync: %s", result.as_dict())
    return result
