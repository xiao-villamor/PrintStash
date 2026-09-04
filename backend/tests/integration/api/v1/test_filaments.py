"""The local filament-preset catalog, and the presets Spoolman owns.

A preset carries the cost-per-kg every print estimate is derived from, so writes are
admin-only while reads are open to anyone signed in. The rule worth naming is the
Spoolman one: a preset mirroring a Spoolman filament is **read-only here**. Editing it
locally would be overwritten on the next sync and deleting it would just make it
reappear, so both answer 409 `filament_profile_linked` and point the operator at
Spoolman, which is the source of truth.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.db.models import FilamentProfile, FileType, Metadata
from tests.factories import build_file, build_model
from tests.integration.conftest import UserHeaders

MAX_NAME = 128


@pytest.fixture
def make_profile(db_session: Session):
    def build(name: str, **overrides: Any) -> FilamentProfile:
        row = FilamentProfile(name=name, **overrides)
        db_session.add(row)
        db_session.commit()
        db_session.refresh(row)
        return row

    return build


@pytest.fixture
def spoolman_profile(make_profile) -> FilamentProfile:
    return make_profile("Spoolman PLA", material_type="PLA", spoolman_filament_id=42)


def _create(client: TestClient, headers: dict[str, str], **overrides: Any):
    body: dict[str, Any] = {
        "name": "Prusament PLA",
        "material_type": "PLA",
        "material_brand": "Prusa",
        "cost_per_kg": 29.99,
    }
    body.update(overrides)
    return client.post("/api/v1/filament-profiles", headers=headers, json=body)


class TestListFilamentProfiles:
    def test_lists_the_stored_presets(
        self, client: TestClient, auth_headers: dict[str, str], make_profile
    ) -> None:
        make_profile("Prusament PLA", material_type="PLA", cost_per_kg=29.99)

        response = client.get("/api/v1/filament-profiles", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json()[0]["cost_per_kg"] == 29.99

    def test_orders_presets_by_name(
        self, client: TestClient, auth_headers: dict[str, str], make_profile
    ) -> None:
        for name in ("Zebra PETG", "Amazon PLA", "Prusament PLA"):
            make_profile(name)

        listed = client.get("/api/v1/filament-profiles", headers=auth_headers).json()

        assert [row["name"] for row in listed] == [
            "Amazon PLA",
            "Prusament PLA",
            "Zebra PETG",
        ]

    def test_reports_how_many_files_use_each_preset(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        make_profile,
    ) -> None:
        profile = make_profile("Prusament PLA", material_type="PLA")
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
        db_session.add(
            Metadata(
                file_id=gcode.id, material_type="PLA", filament_name="Prusament PLA"
            )
        )
        db_session.commit()

        listed = client.get("/api/v1/filament-profiles", headers=auth_headers).json()

        assert listed[0]["id"] == profile.id
        assert listed[0]["usage_count"] == 1

    def test_returns_an_empty_list_with_no_presets(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        assert (
            client.get("/api/v1/filament-profiles", headers=auth_headers).json() == []
        )

    def test_allows_any_signed_in_user(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        response = client.get(
            "/api/v1/filament-profiles", headers=user_headers("reader", scope="read")
        )

        assert response.status_code == 200

    def test_rejects_an_unauthenticated_caller(self, client: TestClient) -> None:
        assert client.get("/api/v1/filament-profiles").status_code == 401


class TestCreateFilamentProfile:
    def test_returns_the_created_preset(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = _create(client, auth_headers)

        assert response.status_code == 201, response.text
        assert response.json()["name"] == "Prusament PLA"

    def test_persists_the_preset(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        created = _create(client, auth_headers).json()

        assert db_session.get(FilamentProfile, created["id"]) is not None

    def test_trims_the_name(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        assert _create(client, auth_headers, name="  Prusament PLA ").json()[
            "name"
        ] == ("Prusament PLA")

    def test_rejects_a_duplicate_name(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        _create(client, auth_headers, name="Dup Filament")

        duplicate = _create(client, auth_headers, name="Dup Filament")

        assert duplicate.status_code == 409, duplicate.text
        assert duplicate.json()["detail"] == "filament_profile_already_exists"

    def test_rejects_a_name_outside_the_length_bounds(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        assert (
            _create(client, auth_headers, name="x" * (MAX_NAME + 1)).status_code == 422
        )

    def test_rejects_a_negative_cost(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        assert _create(client, auth_headers, cost_per_kg=-1).status_code == 422

    def test_rejects_an_unknown_field(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        assert _create(client, auth_headers, unexpected="ignored").status_code == 422

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders
    ) -> None:
        assert _create(client, user_headers("operator")).status_code == 403


class TestUpdateFilamentProfile:
    def test_edits_the_named_fields(
        self, client: TestClient, auth_headers: dict[str, str], make_profile
    ) -> None:
        profile = make_profile("Prusament PLA")

        response = client.patch(
            f"/api/v1/filament-profiles/{profile.id}",
            headers=auth_headers,
            json={"cost_per_kg": 24.5, "notes": "Bulk pack"},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["cost_per_kg"] == 24.5
        assert body["notes"] == "Bulk pack"

    def test_trims_edited_text_fields(
        self, client: TestClient, auth_headers: dict[str, str], make_profile
    ) -> None:
        profile = make_profile("Prusament PLA")

        body = client.patch(
            f"/api/v1/filament-profiles/{profile.id}",
            headers=auth_headers,
            json={"material_type": "  PETG  ", "material_brand": "  Prusa  "},
        ).json()

        assert body["material_type"] == "PETG"
        assert body["material_brand"] == "Prusa"

    def test_clears_a_field_sent_as_blank(
        self, client: TestClient, auth_headers: dict[str, str], make_profile
    ) -> None:
        profile = make_profile("Prusament PLA", material_brand="Prusa")

        body = client.patch(
            f"/api/v1/filament-profiles/{profile.id}",
            headers=auth_headers,
            json={"material_brand": "   "},
        ).json()

        assert body["material_brand"] is None

    def test_leaves_unsent_fields_alone(
        self, client: TestClient, auth_headers: dict[str, str], make_profile
    ) -> None:
        profile = make_profile("Prusament PLA", material_brand="Prusa")

        body = client.patch(
            f"/api/v1/filament-profiles/{profile.id}",
            headers=auth_headers,
            json={"notes": "only the notes"},
        ).json()

        assert body["material_brand"] == "Prusa"

    def test_rejects_a_rename_onto_another_preset(
        self, client: TestClient, auth_headers: dict[str, str], make_profile
    ) -> None:
        make_profile("Filament A")
        target = make_profile("Filament B")

        response = client.patch(
            f"/api/v1/filament-profiles/{target.id}",
            headers=auth_headers,
            json={"name": "Filament A"},
        )

        assert response.status_code == 409, response.text

    def test_allows_a_rename_to_its_own_name(
        self, client: TestClient, auth_headers: dict[str, str], make_profile
    ) -> None:
        profile = make_profile("Filament A")

        response = client.patch(
            f"/api/v1/filament-profiles/{profile.id}",
            headers=auth_headers,
            json={"name": "Filament A"},
        )

        assert response.status_code == 200, "a preset does not conflict with itself"

    def test_refuses_to_edit_a_spoolman_linked_preset(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        spoolman_profile: FilamentProfile,
    ) -> None:
        response = client.patch(
            f"/api/v1/filament-profiles/{spoolman_profile.id}",
            headers=auth_headers,
            json={"cost_per_kg": 99},
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "filament_profile_linked"

    def test_leaves_a_spoolman_linked_preset_unchanged(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        spoolman_profile: FilamentProfile,
    ) -> None:
        client.patch(
            f"/api/v1/filament-profiles/{spoolman_profile.id}",
            headers=auth_headers,
            json={"cost_per_kg": 99},
        )

        db_session.expire_all()
        assert db_session.get(FilamentProfile, spoolman_profile.id).cost_per_kg is None

    def test_reports_an_unknown_preset_as_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.patch(
            "/api/v1/filament-profiles/999", headers=auth_headers, json={"notes": "x"}
        )

        assert response.status_code == 404, response.text
        assert response.json()["detail"] == "filament_profile_not_found"

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders, make_profile
    ) -> None:
        profile = make_profile("Filament A")

        response = client.patch(
            f"/api/v1/filament-profiles/{profile.id}",
            headers=user_headers("operator"),
            json={"notes": "x"},
        )

        assert response.status_code == 403, response.text


class TestDeleteFilamentProfile:
    def test_deletes_the_preset(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        make_profile,
    ) -> None:
        profile = make_profile("Deletable Filament")
        profile_id = profile.id

        response = client.delete(
            f"/api/v1/filament-profiles/{profile_id}", headers=auth_headers
        )

        assert response.status_code == 204, response.text
        db_session.expire_all()
        assert db_session.get(FilamentProfile, profile_id) is None

    def test_refuses_to_delete_a_spoolman_linked_preset(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        spoolman_profile: FilamentProfile,
    ) -> None:
        response = client.delete(
            f"/api/v1/filament-profiles/{spoolman_profile.id}", headers=auth_headers
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "filament_profile_linked"

    def test_keeps_a_spoolman_linked_preset(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        spoolman_profile: FilamentProfile,
    ) -> None:
        client.delete(
            f"/api/v1/filament-profiles/{spoolman_profile.id}", headers=auth_headers
        )

        db_session.expire_all()
        # A local delete would just reappear on the next sync.
        assert db_session.get(FilamentProfile, spoolman_profile.id) is not None

    def test_reports_an_unknown_preset_as_not_found(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.delete("/api/v1/filament-profiles/999", headers=auth_headers)

        assert response.status_code == 404, response.text

    def test_rejects_a_non_superuser(
        self, client: TestClient, user_headers: UserHeaders, make_profile
    ) -> None:
        profile = make_profile("Filament A")

        response = client.delete(
            f"/api/v1/filament-profiles/{profile.id}", headers=user_headers("operator")
        )

        assert response.status_code == 403, response.text
