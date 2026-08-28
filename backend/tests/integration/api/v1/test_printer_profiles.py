"""The local printer-preset catalog.

Presets are what a model's slicer metadata is matched against, so the list is read by
anyone signed in and written only by an admin. A name is the identity: it is unique,
trimmed on the way in, and a rename onto another preset's name is a 409 rather than a
silent merge that would re-point every model matched to it.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import (
    FileType,
    Metadata,
    PrinterProfile,
)
from tests.factories import build_file, build_model
from tests.integration.conftest import UserHeaders

MAX_NAME = 128


@pytest.fixture
def make_profile(db_session: Session):
    def build(name: str, **overrides: Any) -> PrinterProfile:
        row = PrinterProfile(name=name, **overrides)
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        return row

    return build


def _create(client: TestClient, headers: dict[str, str], **overrides: Any):
    body: dict[str, Any] = {
        "name": "Voron 2.4",
        "printer_model": "Voron 2.4 350 Klipper",
        "slicer_name": "OrcaSlicer",
        "nozzle_diameter_mm": 0.4,
    }
    body.update(overrides)
    return client.post("/api/v1/printer-profiles", headers=headers, json=body)


class TestListPrinterProfiles:
    def test_lists_the_stored_presets(
        self, client: TestClient, auth_headers: dict[str, str], make_profile
    ) -> None:
        make_profile("Voron 2.4", printer_model="Voron 2.4 350 Klipper")

        response = client.get("/api/v1/printer-profiles", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()[0]["printer_model"] == "Voron 2.4 350 Klipper"

    def test_orders_presets_by_name(
        self, client: TestClient, auth_headers: dict[str, str], make_profile
    ) -> None:
        for name in ("Voron", "Ender-3", "Prusa MK4"):
            make_profile(name)

        listed = client.get("/api/v1/printer-profiles", headers=auth_headers).json()

        assert [row["name"] for row in listed] == ["Ender-3", "Prusa MK4", "Voron"]

    def test_reports_how_many_files_use_each_preset(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        make_profile,
    ) -> None:
        profile = make_profile("Ender-3", printer_model="Ender-3")
        model = build_model(db_session, name="Bracket", slug="bracket", hash="a" * 64)
        gcode = build_file(
            db_session,
            model,
            path="bracket.gcode",
            filename="bracket.gcode",
            file_type=FileType.GCODE,
            version=1,
            size_bytes=10,
            sha256="b" * 64,
        )
        db_session.add(Metadata(file_id=gcode.id, printer_model="Ender-3"))
        db_session.commit()

        listed = client.get("/api/v1/printer-profiles", headers=auth_headers).json()

        assert listed[0]["id"] == profile.id
        assert listed[0]["usage_count"] == 1

    def test_returns_an_empty_list_with_no_presets(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        assert client.get("/api/v1/printer-profiles", headers=auth_headers).json() == []

    def test_allows_any_signed_in_user(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        response = client.get(
            "/api/v1/printer-profiles", headers=user_headers("reader", scope="read")
        )

        assert response.status_code == 200, "presets are read by everyone who signs in"

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/printer-profiles").status_code == 401


class TestCreatePrinterProfile:
    def test_returns_the_created_preset(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = _create(client, auth_headers)

        assert response.status_code == 201, response.text
        assert response.json()["name"] == "Voron 2.4"

    def test_persists_the_preset(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        created = _create(client, auth_headers).json()

        assert db_session.get(PrinterProfile, created["id"]) is not None

    def test_trims_the_name(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        body = _create(client, auth_headers, name="  Voron 2.4  ").json()

        assert body["name"] == "Voron 2.4"

    def test_rejects_a_duplicate_name(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        _create(client, auth_headers, name="Dup Printer")

        duplicate = _create(client, auth_headers, name="Dup Printer")

        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json()["detail"] == "printer_profile_already_exists"

    def test_rejects_a_name_outside_the_length_bounds(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        assert (
            _create(client, auth_headers, name="x" * (MAX_NAME + 1)).status_code == 422
        )

    def test_rejects_a_negative_nozzle_diameter(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        assert _create(client, auth_headers, nozzle_diameter_mm=-1).status_code == 422

    def test_rejects_an_unknown_field(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        assert _create(client, auth_headers, unexpected="ignored").status_code == 422

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        assert _create(client, user_headers("operator")).status_code == 403


class TestUpdatePrinterProfile:
    def test_edits_the_named_fields(
        self, client: TestClient, auth_headers: dict[str, str], make_profile
    ) -> None:
        profile = make_profile("Voron 2.4")

        response = client.patch(
            f"/api/v1/printer-profiles/{profile.id}",
            headers=auth_headers,
            json={"notes": "Garage enclosed printer", "nozzle_diameter_mm": 0.6},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["notes"] == "Garage enclosed printer"
        assert body["nozzle_diameter_mm"] == 0.6

    def test_trims_edited_text_fields(
        self, client: TestClient, auth_headers: dict[str, str], make_profile
    ) -> None:
        profile = make_profile("Printer B")

        body = client.patch(
            f"/api/v1/printer-profiles/{profile.id}",
            headers=auth_headers,
            json={"printer_model": "  Prusa MK4  ", "slicer_name": "  PrusaSlicer  "},
        ).json()

        assert body["printer_model"] == "Prusa MK4"
        assert body["slicer_name"] == "PrusaSlicer"

    def test_clears_a_field_sent_as_blank(
        self, client: TestClient, auth_headers: dict[str, str], make_profile
    ) -> None:
        profile = make_profile("Printer B", slicer_name="OrcaSlicer")

        body = client.patch(
            f"/api/v1/printer-profiles/{profile.id}",
            headers=auth_headers,
            json={"slicer_name": "   "},
        ).json()

        assert body["slicer_name"] is None

    def test_leaves_unsent_fields_alone(
        self, client: TestClient, auth_headers: dict[str, str], make_profile
    ) -> None:
        profile = make_profile("Printer B", slicer_name="OrcaSlicer")

        body = client.patch(
            f"/api/v1/printer-profiles/{profile.id}",
            headers=auth_headers,
            json={"notes": "only the notes"},
        ).json()

        assert body["slicer_name"] == "OrcaSlicer"

    def test_rejects_a_rename_onto_another_preset(
        self, client: TestClient, auth_headers: dict[str, str], make_profile
    ) -> None:
        make_profile("Printer A")
        target = make_profile("Printer B")

        response = client.patch(
            f"/api/v1/printer-profiles/{target.id}",
            headers=auth_headers,
            json={"name": "Printer A"},
        )

        assert response.status_code == 409, response.text

    def test_allows_a_rename_to_its_own_name(
        self, client: TestClient, auth_headers: dict[str, str], make_profile
    ) -> None:
        profile = make_profile("Printer A")

        response = client.patch(
            f"/api/v1/printer-profiles/{profile.id}",
            headers=auth_headers,
            json={"name": "Printer A"},
        )

        assert response.status_code == 200, "a preset does not conflict with itself"

    def test_reports_an_unknown_preset_as_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.patch(
            "/api/v1/printer-profiles/999", headers=auth_headers, json={"notes": "x"}
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "printer_profile_not_found"

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders, make_profile
    ) -> None:
        profile = make_profile("Printer A")

        response = client.patch(
            f"/api/v1/printer-profiles/{profile.id}",
            headers=user_headers("operator"),
            json={"notes": "x"},
        )

        assert response.status_code == 403, response.text


class TestDeletePrinterProfile:
    def test_deletes_the_preset(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        make_profile,
    ) -> None:
        profile = make_profile("Deletable Printer")
        profile_id = profile.id

        response = client.delete(
            f"/api/v1/printer-profiles/{profile_id}", headers=auth_headers
        )

        assert response.status_code == 204, response.text
        db_session.expire_all()
        assert db_session.get(PrinterProfile, profile_id) is None

    def test_reports_an_unknown_preset_as_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.delete("/api/v1/printer-profiles/999", headers=auth_headers)

        assert response.status_code == 404, response.text

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders, make_profile
    ) -> None:
        profile = make_profile("Printer A")

        response = client.delete(
            f"/api/v1/printer-profiles/{profile.id}", headers=user_headers("operator")
        )

        assert response.status_code == 403, response.text
