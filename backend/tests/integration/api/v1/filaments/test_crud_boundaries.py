"""Filament profile API authorization, validation, and read-model boundaries."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import FilamentProfile, File, FileType, Metadata, Model, User
from app.services.auth import create_access_token, hash_password


def _regular_headers(session: Session) -> dict[str, str]:
    user = User(
        username="filament-reader",
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=False,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    token = create_access_token(user.id, user.username, scope="write")
    return {"Authorization": f"Bearer {token}"}


class TestListFilamentProfiles:
    def test_list_filament_profiles_empty(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        # Act
        response = client.get("/api/v1/filament-profiles", headers=auth_headers)

        # Assert
        assert response.status_code == 200
        assert response.json() == []

    def test_list_filament_profiles_returns_usage_counts(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        # Arrange
        used = FilamentProfile(
            name="Generic PLA", material_type="PLA", material_brand="Generic"
        )
        unused = FilamentProfile(name="ABS", material_type="ABS")
        model = Model(name="Bracket", slug="bracket", hash="a" * 64)
        db_session.add(used)
        db_session.add(unused)
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)
        artifact = File(
            model_id=model.id,
            path="profile-usage.gcode",
            original_filename="profile-usage.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=1,
            sha256="b" * 64,
        )
        db_session.add(artifact)
        db_session.commit()
        db_session.refresh(artifact)
        db_session.add(
            Metadata(
                file_id=artifact.id,
                material_type="PLA",
                material_brand="Generic",
            )
        )
        db_session.commit()

        # Act
        response = client.get("/api/v1/filament-profiles", headers=auth_headers)

        # Assert
        assert response.status_code == 200
        profiles = {item["name"]: item for item in response.json()}
        assert profiles["Generic PLA"]["usage_count"] == 1
        assert profiles["ABS"]["usage_count"] == 0


class TestCreateFilamentProfile:
    def test_create_filament_profile_accepts_minimal_payload(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        # Act
        response = client.post(
            "/api/v1/filament-profiles",
            headers=auth_headers,
            json={"name": "  PLA Basic  "},
        )

        # Assert
        assert response.status_code == 201
        assert response.json()["name"] == "PLA Basic"
        assert response.json()["material_type"] is None
        assert response.json()["cost_per_kg"] is None

    def test_create_filament_profile_rejects_a_negative_cost(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/filament-profiles",
            headers=auth_headers,
            json={"name": "PLA", "cost_per_kg": -0.01},
        )

        assert response.status_code == 422

    def test_create_filament_profile_rejects_an_unknown_field(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/filament-profiles",
            headers=auth_headers,
            json={"name": "PLA", "unexpected": True},
        )

        assert response.status_code == 422

    def test_create_filament_profile_requires_authentication(
        self, client: TestClient, db_session: Session
    ) -> None:
        response = client.post("/api/v1/filament-profiles", json={"name": "PLA"})

        assert response.status_code == 401
        assert db_session.exec(select(FilamentProfile)).all() == []

    def test_create_filament_profile_requires_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        regular_headers = _regular_headers(db_session)

        response = client.post(
            "/api/v1/filament-profiles",
            headers=regular_headers,
            json={"name": "PLA"},
        )

        assert response.status_code == 403
        assert db_session.exec(select(FilamentProfile)).all() == []


class TestUpdateFilamentProfile:
    def test_update_filament_profile_clears_nullable_fields(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        # Arrange
        profile = FilamentProfile(
            name="PETG", material_type="PETG", material_brand="Maker", notes="old"
        )
        db_session.add(profile)
        db_session.commit()
        db_session.refresh(profile)

        # Act
        response = client.patch(
            f"/api/v1/filament-profiles/{profile.id}",
            headers=auth_headers,
            json={"material_type": None, "material_brand": None, "notes": None},
        )

        # Assert
        assert response.status_code == 200
        assert response.json()["material_type"] is None
        assert response.json()["material_brand"] is None
        assert response.json()["notes"] is None

    def test_update_filament_profile_rejects_invalid_boundaries(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        # Arrange
        profile = FilamentProfile(name="PLA", cost_per_kg=20)
        db_session.add(profile)
        db_session.commit()
        db_session.refresh(profile)

        # Act
        response = client.patch(
            f"/api/v1/filament-profiles/{profile.id}",
            headers=auth_headers,
            json={"cost_per_kg": -1},
        )

        # Assert
        assert response.status_code == 422
        db_session.refresh(profile)
        assert profile.cost_per_kg == 20

    def test_update_filament_profile_requires_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        # Arrange
        profile = FilamentProfile(name="PETG", material_type="PETG")
        db_session.add(profile)
        db_session.commit()
        db_session.refresh(profile)
        regular_headers = _regular_headers(db_session)

        # Act
        response = client.patch(
            f"/api/v1/filament-profiles/{profile.id}",
            headers=regular_headers,
            json={"name": "Changed"},
        )

        # Assert
        assert response.status_code == 403
        db_session.refresh(profile)
        assert profile.name == "PETG"


class TestDeleteFilamentProfile:
    def test_delete_filament_profile_requires_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        # Arrange
        profile = FilamentProfile(name="ASA", material_type="ASA")
        db_session.add(profile)
        db_session.commit()
        db_session.refresh(profile)
        regular_headers = _regular_headers(db_session)

        # Act
        response = client.delete(
            f"/api/v1/filament-profiles/{profile.id}", headers=regular_headers
        )

        # Assert
        assert response.status_code == 403
        assert db_session.get(FilamentProfile, profile.id) is not None
