from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import File, FileType, FilamentProfile, Metadata, Model


def test_filament_profile_crud(client: TestClient, auth_headers: dict[str, str]) -> None:
    created = client.post(
        "/api/v1/filament-profiles",
        headers=auth_headers,
        json={
            "name": "Generic PLA",
            "material_type": "PLA",
            "material_brand": "Generic",
            "cost_per_kg": 20,
        },
    )
    assert created.status_code == 201
    assert created.json()["name"] == "Generic PLA"

    listed = client.get("/api/v1/filament-profiles")
    assert listed.status_code == 200
    assert listed.json()[0]["cost_per_kg"] == 20


def test_model_metadata_estimates_cost_from_local_filament_profile(
    client: TestClient,
    db_session: Session,
) -> None:
    model = Model(name="Bracket", slug="bracket", hash="a" * 64)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)

    file_row = File(
        model_id=model.id,
        path="/tmp/bracket.gcode",
        original_filename="bracket.gcode",
        file_type=FileType.GCODE,
        version=1,
        size_bytes=123,
        sha256="b" * 64,
    )
    db_session.add(file_row)
    db_session.commit()
    db_session.refresh(file_row)

    db_session.add(
        Metadata(
            file_id=file_row.id,
            material_type="PLA",
            material_brand="Generic PLA",
            filament_weight_g=10,
        )
    )
    db_session.add(
        FilamentProfile(
            name="Generic PLA",
            material_type="PLA",
            material_brand="Generic",
            cost_per_kg=20,
        )
    )
    db_session.commit()

    response = client.get(f"/api/v1/models/{model.id}")
    assert response.status_code == 200
    metadata = response.json()["files"][0]["metadata"]
    assert metadata["filament_cost"] == 0.2
