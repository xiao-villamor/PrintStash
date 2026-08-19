from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import (
    ArtifactMaterialRequirement,
    CompatibilityPolicy,
    FileType,
    JobPriority,
    MaterialSource,
    Metadata,
    OperatorGateState,
    Printer,
    PrinterMaterialSlot,
    PrinterProvider,
    PrinterStatus,
    PrinterTool,
    PrintJob,
    PrintJobState,
    RoutingStrategy,
    User,
)
from app.schemas.fleet import (
    BatchCreate,
    FleetSummary,
    MaintenanceWindowCreate,
    QueueJobCreate,
    QueueJobUpdate,
)
from app.schemas.materials import (
    ManualMaterialStateUpdate,
    MaterialSlotWrite,
    MaterialToolWrite,
)
from app.services import fleet, materials
from tests.test_fleet_api import _gcode


def test_material_and_fleet_schema_validation_edges() -> None:
    assert MaterialSlotWrite(slot_key="feed", label="Feed", color_hex=" ").color_hex is None
    assert (
        MaterialSlotWrite(slot_key="feed", label="Feed", color_hex="a1b2c3").color_hex
        == "#A1B2C3"
    )
    with pytest.raises(ValueError, match="material_color_invalid"):
        MaterialSlotWrite(slot_key="feed", label="Feed", color_hex="not-a-color")
    with pytest.raises(ValueError, match="printer_id_required"):
        QueueJobCreate(file_id=1)
    with pytest.raises(ValueError, match="printer_id_required"):
        BatchCreate(file_id=1, quantity=1, strategy=RoutingStrategy.MANUAL)
    with pytest.raises(ValueError, match="automatic_batch_spool_not_allowed"):
        BatchCreate(file_id=1, quantity=1, spool_id=1)
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="maintenance_window_invalid"):
        MaintenanceWindowCreate(starts_at=now, ends_at=now)


def _user(session: Session) -> User:
    user = User(
        username="material-operator", hashed_password="unused", is_superuser=True
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _requirements(session: Session, material: str = "PLA", nozzle: float = 0.4):
    artifact = _gcode(session)
    session.add(
        Metadata(
            file_id=artifact.id,
            material_type=material,
            nozzle_diameter_mm=nozzle,
        )
    )
    session.add(
        ArtifactMaterialRequirement(
            file_id=artifact.id,
            tool_index=0,
            material_type=material,
            color_hex="#FF0000",
        )
    )
    session.commit()
    return artifact


def test_manual_material_state_drives_compatibility_and_confirmation(
    db_session: Session,
) -> None:
    user = _user(db_session)
    printer = Printer(
        name="Material aware",
        moonraker_url="http://material-aware",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    artifact = _requirements(db_session, material="PLA")

    state = materials.replace_manual_state(
        db_session,
        int(printer.id),
        ManualMaterialStateUpdate(
            tools=[
                MaterialToolWrite(
                    tool_key="tool0", label="Tool 0", nozzle_diameter_mm=0.4
                )
            ],
            slots=[
                MaterialSlotWrite(
                    slot_key="slot0",
                    label="Main spool",
                    tool_key="tool0",
                    state="loaded",
                    material_type="ABS",
                    color_hex="#000000",
                )
            ],
        ),
        user,
    )

    assert state.slots[0].source == MaterialSource.MANUAL
    result = materials.compatibility_for_printer(
        db_session, int(artifact.id), int(printer.id)
    )
    assert result.verdict == "mismatch"
    assert result.reasons == ("material_type_mismatch",)
    mismatch_snapshot = fleet.build_routing_snapshot(db_session, {int(artifact.id)})
    with pytest.raises(fleet.FleetError, match="material_mismatch_confirmation_required"):
        fleet.create_batch(
            db_session,
            BatchCreate(
                file_id=int(artifact.id),
                quantity=1,
                strategy=RoutingStrategy.MANUAL,
                printer_id=int(printer.id),
            ),
            user,
        )
    with pytest.raises(fleet.FleetError, match="printer_not_found"):
        fleet.choose_printer(
            db_session,
            RoutingStrategy.MANUAL,
            999_999,
            snapshot=mismatch_snapshot,
            file_id=int(artifact.id),
        )
    printer.is_default = True
    db_session.add(printer)
    db_session.commit()
    mismatch_snapshot = fleet.build_routing_snapshot(db_session, {int(artifact.id)})
    assert fleet.choose_printer(
        db_session,
        RoutingStrategy.DEFAULT,
        None,
        snapshot=mismatch_snapshot,
        file_id=int(artifact.id),
    )[1] == "no_material_compatible_printer"
    try:
        fleet.enqueue_job(
            db_session,
            QueueJobCreate(
                file_id=int(artifact.id),
                strategy="manual",
                printer_id=int(printer.id),
            ),
            user,
        )
    except fleet.FleetError as exc:
        assert exc.code == "material_mismatch_confirmation_required"
    else:  # pragma: no cover - safety assertion
        raise AssertionError("known mismatch was accepted without confirmation")

    materials.replace_manual_state(
        db_session,
        int(printer.id),
        ManualMaterialStateUpdate(
            tools=[
                MaterialToolWrite(
                    tool_key="tool0", label="Tool 0", nozzle_diameter_mm=0.6
                )
            ],
            slots=[
                MaterialSlotWrite(
                    slot_key="slot0",
                    label="Main spool",
                    tool_key="tool0",
                    state="loaded",
                    material_type="PLA",
                    color_hex="#FF0000",
                )
            ],
        ),
        user,
    )
    nozzle_result = materials.compatibility_for_printer(
        db_session, int(artifact.id), int(printer.id)
    )
    assert nozzle_result.verdict == "mismatch"
    assert nozzle_result.reasons == ("nozzle_diameter_mismatch",)
    nozzle_snapshot = fleet.build_routing_snapshot(db_session, {int(artifact.id)})
    assert fleet._compatibility_rank(
        printer, int(artifact.id), nozzle_snapshot
    ) == 2


def test_batch_creation_is_atomic_and_spreads_least_busy_copies(
    db_session: Session,
) -> None:
    user = _user(db_session)
    printers = [
        Printer(
            name=f"Batch {index}",
            moonraker_url=f"http://batch-{index}",
            status=PrinterStatus.READY,
        )
        for index in range(2)
    ]
    db_session.add_all(printers)
    db_session.commit()
    artifact = _gcode(db_session)

    batch, jobs = fleet.create_batch(
        db_session,
        BatchCreate(file_id=int(artifact.id), quantity=2, strategy="least_busy"),
        user,
    )

    assert batch.quantity == 2
    assert [job.copy_index for job in jobs] == [1, 2]
    assert len({job.printer_id for job in jobs}) == 2
    summary = FleetSummary(**fleet.fleet_summary(db_session))
    assert {row.name for row in summary.printers} == {"Batch 0", "Batch 1"}
    assert all(row.next_job_id is not None for row in summary.printers)


def test_operator_hold_resolves_gate_and_enables_drain(db_session: Session) -> None:
    user = _user(db_session)
    printer = Printer(
        name="Release gate",
        moonraker_url="http://release-gate",
        status=PrinterStatus.READY,
        operator_release_required=True,
    )
    db_session.add(printer)
    db_session.commit()
    artifact = _gcode(db_session)
    job = PrintJob(
        printer_id=printer.id,
        file_id=artifact.id,
        model_id=artifact.model_id,
        remote_filename="gate.gcode",
        state=PrintJobState.COMPLETED,
        operator_gate_state=OperatorGateState.PENDING,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    decided = fleet.operator_decision(db_session, int(job.id), "hold", user)

    db_session.refresh(printer)
    assert decided.operator_gate_state == OperatorGateState.HELD
    assert printer.drain_mode is True
    assert printer.drain_reason == f"Operator hold after job {job.id}"


def test_material_routing_prefers_compatible_then_unknown_and_color_is_advisory(
    db_session: Session,
) -> None:
    user = _user(db_session)
    pla = Printer(name="PLA", moonraker_url="http://pla", status=PrinterStatus.READY)
    unknown = Printer(
        name="Unknown", moonraker_url="http://unknown", status=PrinterStatus.READY
    )
    abs_printer = Printer(
        name="ABS", moonraker_url="http://abs", status=PrinterStatus.READY
    )
    db_session.add_all([pla, unknown, abs_printer])
    db_session.commit()
    artifact = _requirements(db_session, material="PLA", nozzle=0.4)

    for printer, material, color in (
        (pla, " pla ", "#0000FF"),
        (abs_printer, "ABS", "#FF0000"),
    ):
        materials.replace_manual_state(
            db_session,
            int(printer.id),
            ManualMaterialStateUpdate(
                tools=[
                    MaterialToolWrite(
                        tool_key="tool0", label="Tool 0", nozzle_diameter_mm=0.4
                    )
                ],
                slots=[
                    MaterialSlotWrite(
                        slot_key="feed",
                        label="Feed",
                        tool_key="tool0",
                        state="loaded",
                        material_type=material,
                        color_hex=color,
                    )
                ],
            ),
            user,
        )

    report = materials.compatibility_for_printer(
        db_session, int(artifact.id), int(pla.id)
    )
    assert report.verdict == "compatible"
    assert report.color_advisories

    snapshot = fleet.build_routing_snapshot(db_session, {int(artifact.id)})
    selected, blocked = fleet.choose_printer(
        db_session,
        RoutingStrategy.LEAST_BUSY,
        None,
        snapshot=snapshot,
        file_id=int(artifact.id),
    )
    assert selected is not None and selected.id == pla.id
    assert blocked is None

    pla.drain_mode = True
    db_session.add(pla)
    db_session.commit()
    snapshot = fleet.build_routing_snapshot(db_session, {int(artifact.id)})
    selected, blocked = fleet.choose_printer(
        db_session,
        RoutingStrategy.LEAST_BUSY,
        None,
        snapshot=snapshot,
        file_id=int(artifact.id),
    )
    assert selected is not None and selected.id == unknown.id
    assert blocked is None

    unknown.drain_mode = True
    db_session.add(unknown)
    db_session.commit()
    snapshot = fleet.build_routing_snapshot(db_session, {int(artifact.id)})
    selected, blocked = fleet.choose_printer(
        db_session,
        RoutingStrategy.LEAST_BUSY,
        None,
        snapshot=snapshot,
        file_id=int(artifact.id),
    )
    assert selected is None
    assert blocked == "no_material_compatible_printer"

    selected, blocked = fleet.choose_printer(
        db_session,
        RoutingStrategy.LEAST_BUSY,
        None,
        snapshot=snapshot,
        file_id=int(artifact.id),
        compatibility_policy=CompatibilityPolicy.ALLOW_MISMATCH,
    )
    assert selected is not None and selected.id == abs_printer.id
    assert blocked is None


def test_provider_material_state_is_unknown_offline_and_recovers_on_reconnect(
    db_session: Session,
) -> None:
    printer = Printer(
        name="Telemetry",
        moonraker_url="http://telemetry",
        status=PrinterStatus.OFFLINE,
    )
    db_session.add(printer)
    db_session.commit()
    artifact = _requirements(db_session, material="PLA", nozzle=0.4)
    db_session.add(
        PrinterMaterialSlot(
            printer_id=int(printer.id),
            slot_key="tool0",
            label="Tracked spool",
            tool_key="tool0",
            state="loaded",
            source=MaterialSource.MOONRAKER_SPOOLMAN,
            material_type="PLA",
            observed_at=printer.updated_at,
        )
    )
    db_session.add(
        PrinterTool(
            printer_id=int(printer.id),
            tool_key="tool0",
            label="Tool 0",
            nozzle_diameter_mm=0.4,
            source=MaterialSource.MOONRAKER_SPOOLMAN,
            observed_at=printer.updated_at,
        )
    )
    db_session.commit()

    state = materials.read_material_state(db_session, int(printer.id))
    assert state.slots[0].stale is True
    assert (
        materials.compatibility_for_printer(
            db_session, int(artifact.id), int(printer.id)
        ).verdict
        == "unknown"
    )

    printer.status = PrinterStatus.READY
    db_session.add(printer)
    db_session.commit()
    state = materials.read_material_state(db_session, int(printer.id))
    assert state.slots[0].stale is False
    assert (
        materials.compatibility_for_printer(
            db_session, int(artifact.id), int(printer.id)
        ).verdict
        == "compatible"
    )


def test_unresolved_tracked_spool_is_unknown_not_a_proven_mismatch(
    db_session: Session,
) -> None:
    printer = Printer(
        name="Unresolved spool",
        moonraker_url="http://unresolved",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    artifact = _requirements(db_session, material="PLA", nozzle=0.4)
    db_session.add(
        PrinterMaterialSlot(
            printer_id=int(printer.id),
            slot_key="tool0",
            label="Moonraker active spool",
            tool_key="tool0",
            state="loaded",
            source=MaterialSource.MOONRAKER_SPOOLMAN,
            spool_id=42,
            observed_at=printer.updated_at,
        )
    )
    db_session.add(
        PrinterTool(
            printer_id=int(printer.id),
            tool_key="tool0",
            label="Tool 0",
            nozzle_diameter_mm=0.4,
            source=MaterialSource.MOONRAKER_SPOOLMAN,
            observed_at=printer.updated_at,
        )
    )
    db_session.commit()

    result = materials.compatibility_for_printer(
        db_session, int(artifact.id), int(printer.id)
    )
    assert result.verdict == "unknown"
    assert result.missing_materials == ("pla",)


def test_manual_material_state_validation_and_provider_precedence(
    db_session: Session,
) -> None:
    user = _user(db_session)
    materials.ensure_default_tool(db_session, Printer(name="Unsaved"))
    with pytest.raises(materials.MaterialStateError, match="printer_not_found"):
        materials.read_material_state(db_session, 999_999)
    with pytest.raises(materials.MaterialStateError, match="printer_not_found"):
        materials.replace_manual_state(
            db_session, 999_999, ManualMaterialStateUpdate(), user
        )

    printer = Printer(
        name="Validation",
        provider=PrinterProvider.BAMBU_LAN,
        host="192.0.2.60",
        serial="VALIDATION",
        access_code="code",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    assert fleet._active_maintenance(db_session, int(printer.id)) is False
    original_version = printer.updated_at

    for payload, code in (
        (
            ManualMaterialStateUpdate(
                tools=[MaterialToolWrite(tool_key="tool0", label="One")],
                slots=[
                    MaterialSlotWrite(slot_key="same", label="One"),
                    MaterialSlotWrite(slot_key="same", label="Two"),
                ],
            ),
            "material_slot_duplicate",
        ),
        (
            ManualMaterialStateUpdate(
                slots=[
                    MaterialSlotWrite(
                        slot_key="feed", label="Feed", tool_key="missing"
                    )
                ]
            ),
            "material_slot_tool_unknown",
        ),
        (
            ManualMaterialStateUpdate(
                slots=[
                    MaterialSlotWrite(
                        slot_key="feed", label="Feed", state="loaded"
                    )
                ]
            ),
            "loaded_material_type_required",
        ),
    ):
        with pytest.raises(materials.MaterialStateError, match=code):
            materials.replace_manual_state(db_session, int(printer.id), payload, user)

    materials.replace_manual_state(
        db_session,
        int(printer.id),
        ManualMaterialStateUpdate(
            tools=[MaterialToolWrite(tool_key="tool0", label="Manual")],
            slots=[MaterialSlotWrite(slot_key="feed", label="Manual feed")],
        ),
        user,
    )
    with pytest.raises(materials.MaterialStateError, match="material_state_changed"):
        materials.replace_manual_state(
            db_session,
            int(printer.id),
            ManualMaterialStateUpdate(expected_updated_at=original_version),
            user,
        )

    db_session.add(
        PrinterTool(
            printer_id=int(printer.id),
            tool_key="tool0",
            label="Provider tool",
            source=MaterialSource.BAMBU_AMS,
            nozzle_diameter_mm=0.4,
            observed_at=printer.updated_at,
        )
    )
    db_session.add(
        PrinterMaterialSlot(
            printer_id=int(printer.id),
            slot_key="feed",
            label="Provider feed",
            source=MaterialSource.BAMBU_AMS,
            state="loaded",
            material_type="PLA",
            observed_at=printer.updated_at,
        )
    )
    db_session.commit()
    state = materials.read_material_state(db_session, int(printer.id))
    assert [row.label for row in state.tools] == ["Provider tool"]
    assert [row.label for row in state.slots] == ["Provider feed"]
    assert state.slots[0].confidence == "provider_reported"


def test_compatibility_unknown_inputs_multitool_mapping_and_report(
    db_session: Session,
) -> None:
    user = _user(db_session)
    printer = Printer(
        name="Compatibility edges",
        moonraker_url="http://compatibility-edges",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    no_requirements = _gcode(db_session)
    assert materials.compatibility_for_printer(
        db_session, int(no_requirements.id), int(printer.id)
    ).reasons == ("job_material_unknown",)
    with pytest.raises(materials.MaterialStateError, match="file_not_found"):
        materials.compatibility_for_printer(db_session, 999_999, int(printer.id))
    with pytest.raises(materials.MaterialStateError, match="printer_not_found"):
        materials.compatibility_for_printer(
            db_session, int(no_requirements.id), 999_999
        )

    artifact = no_requirements
    db_session.add(
        Metadata(
            file_id=artifact.id,
            material_type="PLA",
            nozzle_diameter_mm=0.4,
        )
    )
    db_session.add(
        ArtifactMaterialRequirement(
            file_id=artifact.id,
            tool_index=0,
            material_type="PLA",
            color_hex="#FF0000",
        )
    )
    db_session.add(
        ArtifactMaterialRequirement(
            file_id=artifact.id,
            tool_index=1,
            material_type="PETG",
            color_hex="#00FF00",
        )
    )
    db_session.commit()
    materials.replace_manual_state(
        db_session,
        int(printer.id),
        ManualMaterialStateUpdate(
            tools=[MaterialToolWrite(tool_key="tool0", label="Tool 0")],
            slots=[
                MaterialSlotWrite(
                    slot_key="feed",
                    label="Feed",
                    tool_key="tool0",
                    state="loaded",
                    material_type="PLA",
                ),
                MaterialSlotWrite(
                    slot_key="feed-2",
                    label="Feed 2",
                    tool_key="tool0",
                    state="loaded",
                    material_type="PETG",
                ),
            ],
        ),
        user,
    )
    result = materials.compatibility_for_printer(
        db_session, int(artifact.id), int(printer.id)
    )
    assert result.verdict == "unknown"
    assert "tool_feed_mapping_unknown" in result.reasons
    assert "printer_nozzle_unknown" in result.reasons
    snapshot = fleet.build_routing_snapshot(db_session, {int(artifact.id)})
    assert fleet._compatibility_rank(printer, int(artifact.id), snapshot) == 1
    report = materials.compatibility_report(
        db_session, int(artifact.id), [int(printer.id)]
    )
    assert [row.tool_index for row in report.requirements] == [0, 1]
    assert report.printers[0].verdict == "unknown"


def test_material_state_compatibility_batch_and_release_apis(
    client: TestClient,
    auth_headers: dict[str, str],
    db_session: Session,
) -> None:
    user = db_session.exec(select(User).where(User.username == "test-writer")).one()
    printer = Printer(
        name="API material",
        moonraker_url="http://api-material",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    artifact = _requirements(db_session, material="PLA", nozzle=0.4)

    state = client.get(
        f"/api/v1/printers/{printer.id}/material-state", headers=auth_headers
    )
    assert state.status_code == 200
    updated = client.put(
        f"/api/v1/printers/{printer.id}/material-state/manual",
        headers=auth_headers,
        json={
            "expected_updated_at": state.json()["updated_at"],
            "tools": [
                {"tool_key": "tool0", "label": "Tool 0", "nozzle_diameter_mm": 0.4}
            ],
            "slots": [
                {
                    "slot_key": "feed",
                    "label": "Feed",
                    "tool_key": "tool0",
                    "state": "loaded",
                    "material_type": "PLA",
                }
            ],
        },
    )
    assert updated.status_code == 200
    stale = client.put(
        f"/api/v1/printers/{printer.id}/material-state/manual",
        headers=auth_headers,
        json={"expected_updated_at": state.json()["updated_at"]},
    )
    assert stale.status_code == 409
    invalid = client.put(
        f"/api/v1/printers/{printer.id}/material-state/manual",
        headers=auth_headers,
        json={
            "slots": [
                {"slot_key": "same", "label": "One"},
                {"slot_key": "same", "label": "Two"},
            ]
        },
    )
    assert invalid.status_code == 400

    compatibility = client.post(
        "/api/v1/fleet/compatibility",
        headers=auth_headers,
        json={"file_id": artifact.id, "printer_ids": [printer.id]},
    )
    assert compatibility.status_code == 200
    assert compatibility.json()["printers"][0]["verdict"] == "compatible"
    missing = client.post(
        "/api/v1/fleet/compatibility",
        headers=auth_headers,
        json={"file_id": 999_999, "printer_ids": [printer.id]},
    )
    assert missing.status_code == 404
    missing_batch = client.post(
        "/api/v1/fleet/batches",
        headers=auth_headers,
        json={"file_id": 999_999, "quantity": 1, "strategy": "least_busy"},
    )
    assert missing_batch.status_code == 404

    batch = client.post(
        "/api/v1/fleet/batches",
        headers=auth_headers,
        json={"file_id": artifact.id, "quantity": 2, "strategy": "least_busy"},
    )
    assert batch.status_code == 201
    assert len(batch.json()["jobs"]) == 2

    gate = PrintJob(
        printer_id=printer.id,
        file_id=artifact.id,
        model_id=artifact.model_id,
        remote_filename="release.gcode",
        state=PrintJobState.COMPLETED,
        operator_gate_state=OperatorGateState.PENDING,
        requested_by=user.id,
    )
    db_session.add(gate)
    db_session.commit()
    db_session.refresh(gate)
    released = client.post(
        f"/api/v1/fleet/queue/{gate.id}/operator-decision",
        headers=auth_headers,
        json={"action": "release"},
    )
    assert released.status_code == 200
    assert released.json()["operator_gate_state"] == "released"
    repeated = client.post(
        f"/api/v1/fleet/queue/{gate.id}/operator-decision",
        headers=auth_headers,
        json={"action": "release"},
    )
    assert repeated.status_code == 409


def test_batch_queue_and_operator_edge_paths(db_session: Session) -> None:
    user = _user(db_session)
    printer = Printer(
        name="Fleet edges",
        moonraker_url="http://fleet-edges",
        status=PrinterStatus.READY,
        group="room-a",
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    artifact = _gcode(db_session)

    for payload, code in (
        (BatchCreate(file_id=999_999, quantity=1), "file_not_found"),
        (
            BatchCreate(
                file_id=int(artifact.id),
                quantity=101,
            ),
            "batch_quantity_exceeds_limit",
        ),
    ):
        with pytest.raises(fleet.FleetError, match=code):
            fleet.create_batch(db_session, payload, user)

    artifact.file_type = FileType.STL
    db_session.add(artifact)
    db_session.commit()
    with pytest.raises(fleet.FleetError, match="file_not_gcode"):
        fleet.create_batch(
            db_session, BatchCreate(file_id=int(artifact.id), quantity=1), user
        )
    artifact.file_type = FileType.GCODE
    artifact.original_filename = "binary.bgcode"
    db_session.add(artifact)
    db_session.commit()
    with pytest.raises(fleet.FleetError, match="binary_gcode_not_printable"):
        fleet.create_batch(
            db_session, BatchCreate(file_id=int(artifact.id), quantity=1), user
        )

    artifact.original_filename = "cube.gcode"
    db_session.add(artifact)
    db_session.commit()
    first = fleet.enqueue_job(
        db_session,
        QueueJobCreate(
            file_id=int(artifact.id), strategy="manual", printer_id=int(printer.id)
        ),
        user,
    )
    second = fleet.enqueue_job(
        db_session,
        QueueJobCreate(
            file_id=int(artifact.id), strategy="manual", printer_id=int(printer.id)
        ),
        user,
    )
    changed = fleet.update_queue_job(
        db_session,
        int(second.id),
        QueueJobUpdate(
            priority=JobPriority.RUSH,
            queue_position=1,
            target_group="room-a",
            compatibility_policy=CompatibilityPolicy.ALLOW_MISMATCH,
            strategy=RoutingStrategy.MANUAL,
            printer_id=int(printer.id),
        ),
        user,
    )
    assert changed.priority == JobPriority.RUSH
    assert changed.target_group == "room-a"
    assert changed.material_override_by == user.id
    with pytest.raises(fleet.FleetError, match="queue_job_changed"):
        fleet.update_queue_job(
            db_session,
            int(first.id),
            QueueJobUpdate(
                expected_updated_at=printer.updated_at,
                priority=JobPriority.LOW,
            ),
            user,
        )
    with pytest.raises(fleet.FleetError, match="printer_id_required"):
        fleet.update_queue_job(
            db_session,
            int(first.id),
            QueueJobUpdate(strategy=RoutingStrategy.MANUAL),
            user,
        )
    assert fleet.list_queue_page(db_session, visible_printer_ids=set()) == []

    with pytest.raises(fleet.FleetError, match="queue_job_not_found"):
        fleet.operator_decision(db_session, 999_999, "release", user)
    with pytest.raises(fleet.FleetError, match="operator_decision_not_pending"):
        fleet.operator_decision(db_session, int(first.id), "release", user)
    gate = PrintJob(
        file_id=artifact.id,
        model_id=artifact.model_id,
        remote_filename="unassigned.gcode",
        state=PrintJobState.COMPLETED,
        operator_gate_state=OperatorGateState.PENDING,
    )
    db_session.add(gate)
    db_session.commit()
    db_session.refresh(gate)
    with pytest.raises(fleet.FleetError, match="printer_not_found"):
        fleet.operator_decision(db_session, int(gate.id), "release", user)
    gate.printer_id = printer.id
    db_session.add(gate)
    db_session.commit()
    with pytest.raises(fleet.FleetError, match="operator_decision_invalid"):
        fleet.operator_decision(db_session, int(gate.id), "later", user)
    released = fleet.operator_decision(db_session, int(gate.id), "release", user)
    assert released.operator_gate_state == OperatorGateState.RELEASED


def test_tracking_spool_is_resolved_only_when_unambiguous(
    db_session: Session,
) -> None:
    printer = Printer(
        name="Tracking",
        moonraker_url="http://tracking",
        status=PrinterStatus.READY,
    )
    db_session.add(printer)
    db_session.commit()
    db_session.refresh(printer)
    artifact = _requirements(db_session, material="PLA", nozzle=0.4)
    for slot_key, material, spool_id in (
        ("matching", "PLA", 7),
        ("wrong", "ABS", 8),
        ("unknown", None, 9),
    ):
        db_session.add(
            PrinterMaterialSlot(
                printer_id=int(printer.id),
                slot_key=slot_key,
                label=slot_key,
                state="loaded",
                material_type=material,
                spool_id=spool_id,
                spool_name=f"Spool {spool_id}",
                spool_filament_id=spool_id * 10,
                source=MaterialSource.MANUAL,
            )
        )
    db_session.commit()
    snapshot = fleet.build_routing_snapshot(db_session, {int(artifact.id)})
    assert fleet._unambiguous_tracking_spool(
        Printer(name="Unsaved"), int(artifact.id), snapshot
    ) == (None, None, None)
    assert fleet._unambiguous_tracking_spool(
        printer, int(artifact.id), snapshot
    ) == (7, "Spool 7", 70)

    duplicate = db_session.exec(
        select(PrinterMaterialSlot).where(
            PrinterMaterialSlot.printer_id == printer.id,
            PrinterMaterialSlot.slot_key == "wrong",
        )
    ).one()
    duplicate.material_type = "PLA"
    db_session.add(duplicate)
    db_session.commit()
    snapshot = fleet.build_routing_snapshot(db_session, {int(artifact.id)})
    assert fleet._unambiguous_tracking_spool(
        printer, int(artifact.id), snapshot
    ) == (None, None, None)
    assert fleet.list_queue(db_session) == []
