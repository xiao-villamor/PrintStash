from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import FilamentProfile, File, FileType, Metadata, Model
from app.services.profile_detection import upsert_detected_filament_profile


def test_filament_profile_crud(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
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

    listed = client.get("/api/v1/filament-profiles", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()[0]["cost_per_kg"] == 20


def test_create_duplicate_name_conflict(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    payload = {"name": "PETG Basic", "material_type": "PETG"}
    first = client.post(
        "/api/v1/filament-profiles", headers=auth_headers, json=payload
    )
    assert first.status_code == 201
    second = client.post(
        "/api/v1/filament-profiles", headers=auth_headers, json=payload
    )
    assert second.status_code == 409
    assert second.json()["detail"] == "filament_profile_already_exists"


def test_update_not_found(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    resp = client.patch(
        "/api/v1/filament-profiles/999",
        headers=auth_headers,
        json={"cost_per_kg": 25},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "filament_profile_not_found"


def test_update_rename_to_existing_name_conflict(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    a = FilamentProfile(name="Profile A", material_type="PLA")
    b = FilamentProfile(name="Profile B", material_type="PLA")
    db_session.add(a)
    db_session.add(b)
    db_session.commit()
    db_session.refresh(a)
    db_session.refresh(b)

    resp = client.patch(
        f"/api/v1/filament-profiles/{b.id}",
        headers=auth_headers,
        json={"name": "Profile A"},
    )
    assert resp.status_code == 409
    assert resp.json()["detail"] == "filament_profile_already_exists"


def test_update_all_fields_success(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    profile = FilamentProfile(
        name="Old Name",
        material_type="PLA",
        material_brand="Generic",
        cost_per_kg=20,
        notes="old notes",
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    resp = client.patch(
        f"/api/v1/filament-profiles/{profile.id}",
        headers=auth_headers,
        json={
            "name": "New Name",
            "material_type": "  PETG  ",
            "material_brand": "  ",
            "cost_per_kg": 30,
            "notes": "  updated  ",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "New Name"
    assert body["material_type"] == "PETG"
    # blank-after-strip clears the field to null via _clean()
    assert body["material_brand"] is None
    assert body["cost_per_kg"] == 30
    assert body["notes"] == "updated"


def test_update_same_name_does_not_self_conflict(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    profile = FilamentProfile(name="Stays Same", material_type="ABS")
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    resp = client.patch(
        f"/api/v1/filament-profiles/{profile.id}",
        headers=auth_headers,
        json={"name": "Stays Same", "cost_per_kg": 40},
    )
    assert resp.status_code == 200
    assert resp.json()["cost_per_kg"] == 40


def test_delete_not_found(client: TestClient, auth_headers: dict[str, str]) -> None:
    resp = client.delete(
        "/api/v1/filament-profiles/999", headers=auth_headers
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "filament_profile_not_found"


def test_delete_success(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    profile = FilamentProfile(name="Deletable", material_type="PLA")
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)
    profile_id = profile.id

    resp = client.delete(
        f"/api/v1/filament-profiles/{profile_id}", headers=auth_headers
    )
    assert resp.status_code == 204
    db_session.expire_all()
    assert db_session.get(FilamentProfile, profile_id) is None


def test_spoolman_linked_profile_is_read_only(
    client: TestClient, db_session: Session, auth_headers: dict[str, str]
) -> None:
    # A preset mirroring a Spoolman filament must reject local edits/deletes —
    # Spoolman is the source of truth.
    profile = FilamentProfile(
        name="Spoolman PLA", material_type="PLA", spoolman_filament_id=42
    )
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    patched = client.patch(
        f"/api/v1/filament-profiles/{profile.id}",
        headers=auth_headers,
        json={"cost_per_kg": 99},
    )
    assert patched.status_code == 409
    assert patched.json()["detail"] == "filament_profile_linked"

    deleted = client.delete(
        f"/api/v1/filament-profiles/{profile.id}", headers=auth_headers
    )
    assert deleted.status_code == 409


def test_model_metadata_estimates_cost_from_local_filament_profile(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
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

    response = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
    assert response.status_code == 200
    metadata = response.json()["files"][0]["metadata"]
    assert metadata["filament_cost"] == 0.2


def test_model_metadata_profile_cost_overrides_slicer_cost(
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    model = Model(name="Hook", slug="hook", hash="c" * 64)
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)

    file_row = File(
        model_id=model.id,
        path="/tmp/hook.gcode",
        original_filename="hook.gcode",
        file_type=FileType.GCODE,
        version=1,
        size_bytes=123,
        sha256="d" * 64,
    )
    db_session.add(file_row)
    db_session.commit()
    db_session.refresh(file_row)

    db_session.add(
        Metadata(
            file_id=file_row.id,
            material_type="PLA",
            material_brand="ELEGOO",
            filament_weight_g=10,
            filament_cost=0.5,
        )
    )
    db_session.add(
        FilamentProfile(
            name="ELEGOO",
            material_type="PLA",
            material_brand="ELEGOO",
            cost_per_kg=20,
        )
    )
    db_session.commit()

    response = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
    assert response.status_code == 200
    metadata = response.json()["files"][0]["metadata"]
    assert metadata["filament_cost"] == 0.2


def test_detected_filament_profile_infers_cost_per_kg_and_preserves_manual_cost(
    db_session: Session,
) -> None:
    created = upsert_detected_filament_profile(
        db_session,
        {
            "material_type": "PLA",
            "material_brand": "Generic PLA",
            "filament_weight_g": 12.5,
            "filament_cost": 0.35,
        },
    )
    assert created is not None
    assert created.cost_per_kg == 28

    created.cost_per_kg = 22
    db_session.add(created)
    db_session.commit()

    updated = upsert_detected_filament_profile(
        db_session,
        {
            "material_type": "PLA",
            "material_brand": "Generic PLA",
            "filament_weight_g": 10,
            "filament_cost": 0.5,
        },
    )

    assert updated is not None
    assert updated.id == created.id
    assert updated.cost_per_kg == 22


def test_detected_filament_profile_brand_only_meta_creates_profile(
    db_session: Session,
) -> None:
    """material_brand set with no material_type: name falls back to the brand and
    the type-based lookup short-circuits (no material_type to match on)."""
    created = upsert_detected_filament_profile(
        db_session, {"material_brand": "OnlyBrand"}
    )
    assert created is not None
    assert created.name == "OnlyBrand"
    assert created.material_type is None


def test_detected_filament_profile_type_only_meta_creates_profile(
    db_session: Session,
) -> None:
    """material_type set with no brand: the fallback lookup runs the
    material_brand IS NULL branch before creating a fresh profile."""
    created = upsert_detected_filament_profile(db_session, {"material_type": "ABS"})
    assert created is not None
    assert created.name == "ABS"
    assert created.material_brand is None


def test_detected_filament_profile_backfills_missing_fields_on_existing(
    db_session: Session,
) -> None:
    """An existing profile (matched by name) with empty type/cost/notes gets
    those fields filled in from freshly parsed slicer metadata."""
    existing = FilamentProfile(
        name="Generic PLA",
        material_type=None,
        material_brand="Generic PLA",
        cost_per_kg=None,
        notes=None,
    )
    db_session.add(existing)
    db_session.commit()
    db_session.refresh(existing)

    updated = upsert_detected_filament_profile(
        db_session,
        {
            "material_type": "PLA",
            "material_brand": "Generic PLA",
            "filament_weight_g": 10,
            "filament_cost": 0.5,
        },
    )
    assert updated is not None
    assert updated.id == existing.id
    assert updated.material_type == "PLA"
    assert updated.cost_per_kg == 50
    assert updated.notes is not None


def test_detected_filament_profile_backfills_missing_brand_on_existing(
    db_session: Session,
) -> None:
    """A profile whose name is the brand but whose material_brand column is still
    empty gets the brand backfilled from metadata."""
    existing = FilamentProfile(
        name="BrandX",
        material_type="PLA",
        material_brand=None,
        cost_per_kg=12.0,
        notes="kept",
    )
    db_session.add(existing)
    db_session.commit()
    db_session.refresh(existing)

    updated = upsert_detected_filament_profile(
        db_session, {"material_type": "PLA", "material_brand": "BrandX"}
    )
    assert updated is not None
    assert updated.id == existing.id
    assert updated.material_brand == "BrandX"
    assert updated.cost_per_kg == 12.0


def test_detected_filament_profile_returns_none_without_material(
    db_session: Session,
) -> None:
    assert upsert_detected_filament_profile(db_session, {}) is None
