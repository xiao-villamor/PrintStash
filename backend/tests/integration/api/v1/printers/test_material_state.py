"""Defends printer material-state reads and operator replacements.

Routing decisions must see durable, role-checked material state without letting
stale provider observations erase the operator's manual configuration.
"""

from __future__ import annotations

from app.core.time import utcnow
from app.db.models import (
    MaterialSlotState,
    MaterialSource,
    PrinterMaterialSlot,
    PrinterTool,
)

from ._printers_shared import (
    Printer,
    PrinterRole,
    PrinterStatus,
    Session,
    TestClient,
    _grant_printer,
    _user_headers,
    pytest,
    select,
)


def _printer(db_session: Session, *, name: str = "Material printer") -> Printer:
    printer = Printer(name=name, moonraker_url="http://printer.local:7125")
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    return printer


def _manual_payload() -> dict[str, object]:
    return {
        "tools": [
            {
                "tool_key": "tool0",
                "label": "0.4 mm nozzle",
                "nozzle_diameter_mm": 0.4,
            }
        ],
        "slots": [
            {
                "slot_key": "external",
                "label": "External spool",
                "tool_key": "tool0",
                "state": "loaded",
                "material_type": "PLA",
                "color_hex": "ff0000",
            }
        ],
    }


class TestGetPrinterMaterialState:
    def test_returns_operator_maintained_tools_and_slots(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = _printer(db_session)
        replaced = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            json=_manual_payload(),
            headers=auth_headers,
        )

        response = client.get(
            f"/api/v1/printers/{printer.id}/material-state", headers=auth_headers
        )

        assert replaced.status_code == 200, replaced.text
        assert response.status_code == 200, response.text
        assert response.json()["tools"] == [
            {
                "tool_key": "tool0",
                "label": "0.4 mm nozzle",
                "nozzle_diameter_mm": 0.4,
                "source": "manual",
                "observed_at": None,
                "stale": False,
            }
        ]
        assert response.json()["slots"][0] | {"observed_at": None} == {
            "slot_key": "external",
            "label": "External spool",
            "tool_key": "tool0",
            "state": "loaded",
            "source": "manual",
            "confidence": "operator_set",
            "material_type": "PLA",
            "material_brand": None,
            "color_hex": "#FF0000",
            "spool_id": None,
            "spool_name": None,
            "spool_filament_id": None,
            "observed_at": None,
            "stale": False,
        }

    def test_marks_provider_observations_stale_while_offline(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = _printer(db_session, name="Offline material printer")
        printer.status = PrinterStatus.OFFLINE
        provider_slot = PrinterMaterialSlot(
            printer_id=printer.id,
            slot_key="ams-a1",
            label="AMS A1",
            state=MaterialSlotState.LOADED,
            source=MaterialSource.BAMBU_AMS,
            material_type="PETG",
            observed_at=utcnow(),
        )
        db_session.add_all([printer, provider_slot])
        db_session.commit()

        response = client.get(
            f"/api/v1/printers/{printer.id}/material-state", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        provider = next(
            row for row in response.json()["slots"] if row["source"] == "bambu_ams"
        )
        assert provider["stale"] is True

    def test_keeps_manual_state_visible_when_provider_state_is_stale(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = _printer(db_session, name="Manual fallback printer")
        printer.status = PrinterStatus.OFFLINE
        rows = [
            PrinterMaterialSlot(
                printer_id=printer.id,
                slot_key="feed",
                label="Provider feed",
                state=MaterialSlotState.LOADED,
                source=MaterialSource.BAMBU_AMS,
                material_type="PETG",
                observed_at=utcnow(),
            ),
            PrinterMaterialSlot(
                printer_id=printer.id,
                slot_key="feed",
                label="Manual feed",
                state=MaterialSlotState.LOADED,
                source=MaterialSource.MANUAL,
                material_type="PLA",
            ),
        ]
        db_session.add_all([printer, *rows])
        db_session.commit()

        response = client.get(
            f"/api/v1/printers/{printer.id}/material-state", headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert {row["source"] for row in response.json()["slots"]} == {
            "bambu_ams",
            "manual",
        }

    def test_allows_a_view_grant_to_read_material_state(
        self, client: TestClient, db_session: Session
    ) -> None:
        printer = _printer(db_session, name="Shared material printer")
        headers = _user_headers(db_session, "material-viewer")
        _grant_printer(db_session, "material-viewer", printer, PrinterRole.VIEW)

        response = client.get(
            f"/api/v1/printers/{printer.id}/material-state", headers=headers
        )

        assert response.status_code == 200, response.text
        assert response.json()["printer_id"] == printer.id

    def test_returns_not_found_for_a_trashed_printer(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = _printer(db_session, name="Trashed material printer")
        printer.deleted_at = utcnow()
        db_session.add(printer)
        db_session.commit()

        response = client.get(
            f"/api/v1/printers/{printer.id}/material-state", headers=auth_headers
        )

        assert response.status_code == 404
        assert response.json()["detail"] == "printer_not_found"


class TestPutPrinterManualMaterialState:
    def test_replaces_manual_state_in_the_database(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = _printer(db_session)

        response = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            json=_manual_payload(),
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        tools = db_session.exec(
            select(PrinterTool).where(PrinterTool.printer_id == printer.id)
        ).all()
        slots = db_session.exec(
            select(PrinterMaterialSlot).where(
                PrinterMaterialSlot.printer_id == printer.id
            )
        ).all()
        assert [(row.tool_key, row.nozzle_diameter_mm) for row in tools] == [
            ("tool0", 0.4)
        ]
        assert [(row.slot_key, row.material_type) for row in slots] == [
            ("external", "PLA")
        ]

    def test_clears_operator_slots_with_empty_collections(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = _printer(db_session, name="Clearable material printer")
        client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            json=_manual_payload(),
            headers=auth_headers,
        )

        response = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            json={"tools": [], "slots": []},
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["slots"] == []

    def test_rejects_a_stale_optimistic_timestamp(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
    ) -> None:
        printer = _printer(db_session, name="Concurrent material printer")

        response = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            json={
                **_manual_payload(),
                "expected_updated_at": "2020-01-01T00:00:00Z",
            },
            headers=auth_headers,
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "material_state_changed"
        slots = db_session.exec(
            select(PrinterMaterialSlot).where(
                PrinterMaterialSlot.printer_id == printer.id
            )
        ).all()
        assert slots == []

    @pytest.mark.parametrize(
        ("payload", "detail"),
        [
            pytest.param(
                {
                    "tools": [
                        {"tool_key": "tool0", "label": "A"},
                        {"tool_key": "tool0", "label": "B"},
                    ]
                },
                "material_slot_duplicate",
                id="duplicate-tool-key",
            ),
            pytest.param(
                {
                    "tools": [{"tool_key": "tool0", "label": "Tool"}],
                    "slots": [
                        {
                            "slot_key": "feed",
                            "label": "Feed",
                            "tool_key": "tool1",
                        }
                    ],
                },
                "material_slot_tool_unknown",
                id="unknown-tool-reference",
            ),
            pytest.param(
                {
                    "slots": [
                        {
                            "slot_key": "feed",
                            "label": "Feed",
                            "state": "loaded",
                        }
                    ]
                },
                "loaded_material_type_required",
                id="loaded-without-material",
            ),
        ],
    )
    def test_rejects_invalid_material_relationships(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        payload: dict[str, object],
        detail: str,
    ) -> None:
        printer = _printer(db_session, name=f"Invalid {detail}")

        response = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == detail

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(
                {
                    "tools": [
                        {"tool_key": f"tool-{index}", "label": "Tool"}
                        for index in range(17)
                    ]
                },
                id="too-many-tools",
            ),
            pytest.param(
                {
                    "slots": [
                        {"slot_key": f"slot-{index}", "label": "Slot"}
                        for index in range(65)
                    ]
                },
                id="too-many-slots",
            ),
            pytest.param({"unknown": True}, id="unknown-field"),
        ],
    )
    def test_rejects_schema_boundaries(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        payload: dict[str, object],
    ) -> None:
        printer = _printer(db_session, name="Bounded material printer")

        response = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 422

    def test_denies_a_view_role_from_replacing_manual_state(
        self, client: TestClient, db_session: Session
    ) -> None:
        printer = _printer(db_session, name="Read-only material printer")
        headers = _user_headers(db_session, "material-read-only")
        _grant_printer(db_session, "material-read-only", printer, PrinterRole.VIEW)

        response = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            json=_manual_payload(),
            headers=headers,
        )

        assert response.status_code == 403
        assert response.json()["detail"] == "printer_permission_denied"

    def test_allows_a_print_role_to_replace_manual_state(
        self, client: TestClient, db_session: Session
    ) -> None:
        printer = _printer(db_session, name="Operator material printer")
        headers = _user_headers(db_session, "material-operator")
        _grant_printer(db_session, "material-operator", printer, PrinterRole.PRINT)

        response = client.put(
            f"/api/v1/printers/{printer.id}/material-state/manual",
            json=_manual_payload(),
            headers=headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["slots"][0]["material_type"] == "PLA"
