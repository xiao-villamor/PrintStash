"""Shared arrange for the material-state suite.

Every file here needs the same two things: a printer whose loaded filament is
known, and an artifact that asks for a particular filament. Both are three or
four rows, and both are easy to build subtly wrong — a slot with no
`observed_at` reads as stale, an artifact with slicer metadata but no
per-tool requirement rows reads as "material unknown" — so they live here rather
than being reassembled per test.

`load_manual_state` goes through the service rather than inserting rows because
that is the only way to get `MaterialSource.MANUAL` and the `updated_at` bump the
optimistic-concurrency check reads. Inserting the rows directly produces a state
the application cannot actually reach.
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
from app.services import materials
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
        name="Material aware",
        moonraker_url="http://material-aware",
        status=PrinterStatus.READY,
    )


@pytest.fixture
def pla_artifact(db_session: Session) -> File:
    """An artifact asking for PLA through a 0.4 nozzle, on one extruder."""
    return requiring(db_session, material="PLA", nozzle=0.4)


def requiring(
    session: Session,
    *,
    material: str = "PLA",
    nozzle: float = 0.4,
    name: str = "Queue cube",
) -> File:
    """A G-code artifact whose slicer metadata *and* requirement rows agree.

    Both halves are needed. The metadata carries the nozzle diameter the
    compatibility check compares, and the requirement rows carry the per-tool
    material; an artifact with only one of them is `unknown` rather than the
    thing the test meant to build.
    """
    artifact = a_gcode_artifact(session, name)
    build_metadata(session, artifact, material_type=material, nozzle_diameter_mm=nozzle)
    build_material_requirement(session, artifact, material_type=material)
    return artifact


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
