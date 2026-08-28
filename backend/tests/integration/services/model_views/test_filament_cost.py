"""What a print cost, when the slicer and the local preset disagree.

A G-code file carries whatever price its slicer was configured with, which is often a
placeholder someone never changed. The local filament preset carries the price the
operator actually pays. `model_views` resolves the two, and the rule is that the preset
wins: a matched preset's cost-per-kg is applied to the filament weight, replacing the
slicer's figure rather than deferring to it. Get this backwards and every cost in the
library quietly reports a number nobody chose.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import FilamentProfile, FileType, Metadata, Model
from tests.factories import build_file, build_model

GRAMS = 10
COST_PER_KG = 20
EXPECTED_COST = 0.2  # 10 g at 20 per kg


@pytest.fixture
def gcode_with_metadata(db_session: Session):
    """A model with one G-code file whose metadata you choose."""

    def build(name: str, **metadata: Any) -> Model:
        model = build_model(
            db_session, name=name, slug=name.lower(), hash=f"{name:a<64}"[:64]
        )
        gcode = build_file(
            db_session,
            model,
            path=f"{name.lower()}.gcode",
            filename=f"{name.lower()}.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=123,
            sha256=f"{name:b<64}"[:64],
        )
        db_session.add(Metadata(file_id=gcode.id, **metadata))
        db_session.commit()
        return model

    return build


class TestFilamentCost:
    def test_estimates_the_cost_from_a_matching_preset(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        gcode_with_metadata,
    ) -> None:
        model = gcode_with_metadata(
            "Bracket",
            material_type="PLA",
            material_brand="Generic PLA",
            filament_weight_g=GRAMS,
        )
        db_session.add(
            FilamentProfile(
                name="Generic PLA",
                material_type="PLA",
                material_brand="Generic",
                cost_per_kg=COST_PER_KG,
            )
        )
        db_session.commit()

        response = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()["files"][0]["metadata"]["filament_cost"] == EXPECTED_COST

    def test_prefers_the_preset_over_the_slicers_own_figure(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
        gcode_with_metadata,
    ) -> None:
        model = gcode_with_metadata(
            "Hook",
            material_type="PLA",
            material_brand="ELEGOO",
            filament_weight_g=GRAMS,
            filament_cost=0.5,  # what the slicer was configured with
        )
        db_session.add(
            FilamentProfile(
                name="ELEGOO",
                material_type="PLA",
                material_brand="ELEGOO",
                cost_per_kg=COST_PER_KG,
            )
        )
        db_session.commit()

        response = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)

        assert response.json()["files"][0]["metadata"]["filament_cost"] == (
            EXPECTED_COST
        ), "the price the operator pays outranks the slicer's placeholder"
