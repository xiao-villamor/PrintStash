"""Tests for Spoolman → FilamentProfile sync and per-print cost/gram accuracy."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, select

from app.db.models import FilamentProfile
from app.services import filament_sync, runtime_config
from app.services.filament import mm_to_grams
from app.services.filament_sync import cost_per_kg_from_filament
from app.services.spoolman import SpoolmanError


def _filament(fid, name, material="PLA", vendor="Bambu", price=25.0, weight=1000.0,
              density=1.24, diameter=1.75):
    return {
        "id": fid,
        "name": name,
        "material": material,
        "vendor": {"name": vendor},
        "price": price,
        "weight": weight,
        "density": density,
        "diameter": diameter,
    }


def _enable(session: Session):
    runtime_config.set_spoolman_config(session, base_url="http://spoolman.local")
    runtime_config.set_spoolman_enabled(session, True)


def _sync(session: Session, filaments):
    with patch(
        "app.services.filament_sync.get_spoolman_client"
    ) as mock_get:
        mock_get.return_value.list_filaments = AsyncMock(return_value=filaments)
        return asyncio.run(filament_sync.sync_from_spoolman(session))


class TestCostDerivation:
    def test_price_over_weight(self):
        assert cost_per_kg_from_filament({"price": 25.0, "weight": 1000.0}) == 25.0
        assert cost_per_kg_from_filament({"price": 20.0, "weight": 500.0}) == 40.0

    def test_missing_returns_none(self):
        assert cost_per_kg_from_filament({"price": None, "weight": 1000.0}) is None
        assert cost_per_kg_from_filament({"price": 25.0, "weight": 0}) is None


class TestSync:
    def test_creates_linked_profiles(self, db_session: Session):
        _enable(db_session)
        result = _sync(db_session, [_filament(1, "PLA Basic Black")])
        assert result.created == 1
        prof = db_session.exec(
            select(FilamentProfile).where(
                FilamentProfile.spoolman_filament_id == 1
            )
        ).one()
        assert prof.material_type == "PLA"
        assert prof.material_brand == "Bambu"
        assert prof.cost_per_kg == 25.0
        assert prof.density_g_cm3 == 1.24
        assert prof.diameter_mm == 1.75

    def test_adopts_existing_local_preset(self, db_session: Session):
        _enable(db_session)
        db_session.add(
            FilamentProfile(name="PLA Basic Black", material_type="PLA")
        )
        db_session.commit()
        result = _sync(db_session, [_filament(7, "PLA Basic Black")])
        assert result.adopted == 1 and result.created == 0
        # No duplicate row — the local one was linked in place.
        rows = db_session.exec(
            select(FilamentProfile).where(FilamentProfile.name == "PLA Basic Black")
        ).all()
        assert len(rows) == 1
        assert rows[0].spoolman_filament_id == 7

    def test_update_is_idempotent(self, db_session: Session):
        _enable(db_session)
        _sync(db_session, [_filament(1, "PLA Black", price=25.0)])
        result = _sync(db_session, [_filament(1, "PLA Black", price=30.0)])
        assert result.updated == 1 and result.created == 0
        prof = db_session.exec(
            select(FilamentProfile).where(
                FilamentProfile.spoolman_filament_id == 1
            )
        ).one()
        assert prof.cost_per_kg == 30.0

    def test_removed_filament_is_unlinked_not_deleted(self, db_session: Session):
        _enable(db_session)
        _sync(db_session, [_filament(1, "PLA Black")])
        result = _sync(db_session, [])  # filament gone from Spoolman
        assert result.unlinked == 1
        prof = db_session.exec(
            select(FilamentProfile).where(FilamentProfile.name == "PLA Black")
        ).one()
        assert prof.spoolman_filament_id is None  # reverted to local, still present

    def test_disabled_raises(self, db_session: Session):
        with pytest.raises(SpoolmanError):
            asyncio.run(filament_sync.sync_from_spoolman(db_session))


class TestGramAccuracy:
    def test_density_override_changes_grams(self):
        # Same length, different density → different grams.
        base = mm_to_grams(1000.0, "pla")  # table density 1.24
        denser = mm_to_grams(1000.0, "pla", density_g_cm3=1.30)
        assert denser is not None and base is not None
        assert denser > base

    def test_diameter_override_changes_grams(self):
        thin = mm_to_grams(1000.0, "pla", diameter_mm=1.75)
        thick = mm_to_grams(1000.0, "pla", diameter_mm=2.85)
        assert thick is not None and thin is not None
        assert thick > thin
