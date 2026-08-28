"""Shared arrange for the fleet-service suite.

Routing decisions are made against a *snapshot*, not against live rows, so every
file here follows the same rhythm: change the fleet, rebuild the snapshot, ask
again. `snapshot_for` exists so that step reads as one line and never gets
skipped — asking with a stale snapshot returns the previous answer, which looks
like a routing bug and is not.

The material helpers mirror the ones under `services/materials/`, deliberately
duplicated rather than shared: a fleet test wants "this printer is loaded with
X", and reaching across into another suite's conftest for it couples two
directories that otherwise have nothing to say to each other.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session

from app.db.models import File, Printer, PrinterStatus, User
from app.schemas.materials import (
    ManualMaterialStateUpdate,
    MaterialSlotWrite,
    MaterialToolWrite,
)
from app.services import fleet, materials
from tests.factories import (
    a_gcode_artifact,
    build_material_requirement,
    build_metadata,
    build_printer,
    build_user,
)


@pytest.fixture
def operator(db_session: Session) -> User:
    return build_user(db_session, "material-operator", superuser=True)


@pytest.fixture
def printer(db_session: Session) -> Printer:
    return build_printer(
        db_session,
        name="Fleet printer",
        moonraker_url="http://fleet-printer",
        status=PrinterStatus.READY,
    )


@pytest.fixture
def artifact(db_session: Session) -> File:
    """A plain G-code artifact with no material requirements."""
    return a_gcode_artifact(db_session, "Queue cube")


@pytest.fixture
def pla_artifact(db_session: Session) -> File:
    return requiring(db_session, material="PLA", nozzle=0.4)


def requiring(
    session: Session,
    *,
    material: str = "PLA",
    nozzle: float = 0.4,
    name: str = "Queue cube",
) -> File:
    """A G-code artifact whose slicer metadata and requirement rows agree."""
    file = a_gcode_artifact(session, name)
    build_metadata(session, file, material_type=material, nozzle_diameter_mm=nozzle)
    build_material_requirement(session, file, material_type=material)
    return file


def load_manual_state(
    session: Session,
    printer: Printer,
    user: User,
    *,
    material: str = "PLA",
    nozzle: float | None = 0.4,
    color: str | None = "#FF0000",
) -> None:
    """Record what an operator says is loaded, through the service."""
    materials.replace_manual_state(
        session,
        int(printer.id),
        ManualMaterialStateUpdate(
            tools=[
                MaterialToolWrite(
                    tool_key="tool0", label="Tool 0", nozzle_diameter_mm=nozzle
                )
            ],
            slots=[
                MaterialSlotWrite(
                    slot_key="slot0",
                    label="Main spool",
                    tool_key="tool0",
                    state="loaded",
                    material_type=material,
                    color_hex=color,
                )
            ],
        ),
        user,
    )


def snapshot_for(session: Session, file: File) -> fleet.RoutingSnapshot:
    """The routing snapshot as of now, for *file*.

    Routing reads this rather than the live rows, so it must be rebuilt after
    every change the test makes. Reusing a stale one returns the previous answer,
    which reads as a routing bug in the assertion rather than as a stale fixture.
    """
    return fleet.build_routing_snapshot(session, {int(file.id)})


def drain(session: Session, printer: Printer) -> None:
    printer.drain_mode = True
    session.add(printer)
    session.commit()
