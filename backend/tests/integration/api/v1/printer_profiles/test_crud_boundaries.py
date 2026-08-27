"""Printer profile API authorization, validation, and read-model boundaries."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import File, FileType, Metadata, Model, PrinterProfile, User
from app.services.auth import create_access_token, hash_password


def _regular_headers(session: Session) -> dict[str, str]:
    user = User(
        username="printer-profile-reader",
        hashed_password=hash_password("Password123"),
        is_active=True,
        is_superuser=False,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    token = create_access_token(user.id, user.username, scope="write")
    return {"Authorization": f"Bearer {token}"}


class TestListPrinterProfiles:
    def test_list_printer_profiles_empty(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        # Act
        response = client.get("/api/v1/printer-profiles", headers=auth_headers)

        # Assert
        assert response.status_code == 200
        assert response.json() == []

    def test_list_printer_profiles_returns_usage_counts(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        # Arrange
        used = PrinterProfile(name="MK4 preset", printer_model="Prusa MK4")
        unused = PrinterProfile(name="Voron", printer_model="Voron 2.4")
        model = Model(name="Bracket", slug="printer-bracket", hash="c" * 64)
        db_session.add(used)
        db_session.add(unused)
        db_session.add(model)
        db_session.commit()
        db_session.refresh(model)
        artifact = File(
            model_id=model.id,
            path="printer-usage.gcode",
            original_filename="printer-usage.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=1,
            sha256="d" * 64,
        )
        db_session.add(artifact)
        db_session.commit()
        db_session.refresh(artifact)
        db_session.add(Metadata(file_id=artifact.id, printer_model="Prusa MK4"))
        db_session.commit()

        # Act
        response = client.get("/api/v1/printer-profiles", headers=auth_headers)

        # Assert
        assert response.status_code == 200
        profiles = {item["name"]: item for item in response.json()}
        assert profiles["MK4 preset"]["usage_count"] == 1
        assert profiles["Voron"]["usage_count"] == 0


class TestCreatePrinterProfile:
    def test_create_printer_profile_accepts_minimal_payload(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        # Act
        response = client.post(
            "/api/v1/printer-profiles",
            headers=auth_headers,
            json={"name": "  CoreXY  "},
        )

        # Assert
        assert response.status_code == 201
        assert response.json()["name"] == "CoreXY"
        assert response.json()["printer_model"] is None
        assert response.json()["nozzle_diameter_mm"] is None

    def test_create_printer_profile_rejects_a_negative_nozzle_diameter(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/printer-profiles",
            headers=auth_headers,
            json={"name": "Printer", "nozzle_diameter_mm": -0.1},
        )

        assert response.status_code == 422

    def test_create_printer_profile_rejects_an_unknown_field(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.post(
            "/api/v1/printer-profiles",
            headers=auth_headers,
            json={"name": "Printer", "extra": True},
        )

        assert response.status_code == 422

    def test_create_printer_profile_requires_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        # Arrange
        regular_headers = _regular_headers(db_session)

        # Act
        anonymous = client.post("/api/v1/printer-profiles", json={"name": "P1"})
        regular = client.post(
            "/api/v1/printer-profiles",
            headers=regular_headers,
            json={"name": "P1"},
        )

        # Assert
        assert anonymous.status_code == 401
        assert regular.status_code == 403
        assert db_session.exec(select(PrinterProfile)).all() == []


class TestUpdatePrinterProfile:
    def test_update_printer_profile_clears_nullable_fields(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        # Arrange
        profile = PrinterProfile(
            name="MK4",
            printer_model="Prusa MK4",
            slicer_name="PrusaSlicer",
            nozzle_diameter_mm=0.4,
            notes="old",
        )
        db_session.add(profile)
        db_session.commit()
        db_session.refresh(profile)

        # Act
        response = client.patch(
            f"/api/v1/printer-profiles/{profile.id}",
            headers=auth_headers,
            json={
                "printer_model": None,
                "slicer_name": None,
                "nozzle_diameter_mm": None,
                "notes": None,
            },
        )

        # Assert
        assert response.status_code == 200
        assert response.json()["printer_model"] is None
        assert response.json()["slicer_name"] is None
        assert response.json()["nozzle_diameter_mm"] is None
        assert response.json()["notes"] is None

    def test_update_printer_profile_rejects_invalid_boundaries(
        self,
        client: TestClient,
        db_session: Session,
        auth_headers: dict[str, str],
    ) -> None:
        # Arrange
        profile = PrinterProfile(name="MK4", nozzle_diameter_mm=0.4)
        db_session.add(profile)
        db_session.commit()
        db_session.refresh(profile)

        # Act
        response = client.patch(
            f"/api/v1/printer-profiles/{profile.id}",
            headers=auth_headers,
            json={"nozzle_diameter_mm": -0.1},
        )

        # Assert
        assert response.status_code == 422
        db_session.refresh(profile)
        assert profile.nozzle_diameter_mm == 0.4

    def test_update_printer_profile_requires_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        # Arrange
        profile = PrinterProfile(name="MK4", printer_model="Prusa MK4")
        db_session.add(profile)
        db_session.commit()
        db_session.refresh(profile)
        regular_headers = _regular_headers(db_session)

        # Act
        response = client.patch(
            f"/api/v1/printer-profiles/{profile.id}",
            headers=regular_headers,
            json={"name": "Changed"},
        )

        # Assert
        assert response.status_code == 403
        db_session.refresh(profile)
        assert profile.name == "MK4"


class TestDeletePrinterProfile:
    def test_delete_printer_profile_requires_superuser(
        self, client: TestClient, db_session: Session
    ) -> None:
        # Arrange
        profile = PrinterProfile(name="Voron", printer_model="Voron 2.4")
        db_session.add(profile)
        db_session.commit()
        db_session.refresh(profile)
        regular_headers = _regular_headers(db_session)

        # Act
        response = client.delete(
            f"/api/v1/printer-profiles/{profile.id}", headers=regular_headers
        )

        # Assert
        assert response.status_code == 403
        assert db_session.get(PrinterProfile, profile.id) is not None
