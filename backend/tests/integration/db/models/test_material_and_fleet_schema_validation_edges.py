"""Defends material and fleet schema validation edges at the db models integration boundary.

A regression could commit partial, unauthenticated, or internally inconsistent database state.
"""

from __future__ import annotations

from ._material_aware_fleet_shared import (
    BatchCreate,
    CompatibilityPolicy,
    FleetSummary,
    MaintenanceWindowCreate,
    ManualMaterialStateUpdate,
    MaterialSlotWrite,
    MaterialSource,
    MaterialToolWrite,
    OperatorGateState,
    Printer,
    PrinterMaterialSlot,
    PrinterStatus,
    PrinterTool,
    PrintJob,
    PrintJobState,
    QueueJobCreate,
    RoutingStrategy,
    Session,
    _gcode,
    _requirements,
    _user,
    datetime,
    fleet,
    materials,
    pytest,
    timezone,
)


def test_material_and_fleet_schema_validation_edges() -> None:
    assert (
        MaterialSlotWrite(slot_key="feed", label="Feed", color_hex=" ").color_hex
        is None
    )
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
    with pytest.raises(
        fleet.FleetError, match="material_mismatch_confirmation_required"
    ):
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
    assert (
        fleet.choose_printer(
            db_session,
            RoutingStrategy.DEFAULT,
            None,
            snapshot=mismatch_snapshot,
            file_id=int(artifact.id),
        )[1]
        == "no_material_compatible_printer"
    )
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
    assert fleet._compatibility_rank(printer, int(artifact.id), nozzle_snapshot) == 2


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
