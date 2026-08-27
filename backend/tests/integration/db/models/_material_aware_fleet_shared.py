"""Exercise fleet scheduling against persisted material and nozzle requirements.

Failures mean incompatible printers can become eligible for real work.
"""

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
from tests.integration.api.v1.fleet._fleet_shared import _gcode


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


__all__ = [
    "ArtifactMaterialRequirement",
    "BatchCreate",
    "CompatibilityPolicy",
    "FileType",
    "FleetSummary",
    "JobPriority",
    "MaintenanceWindowCreate",
    "ManualMaterialStateUpdate",
    "MaterialSlotWrite",
    "MaterialSource",
    "MaterialToolWrite",
    "Metadata",
    "OperatorGateState",
    "PrintJob",
    "PrintJobState",
    "Printer",
    "PrinterMaterialSlot",
    "PrinterProvider",
    "PrinterStatus",
    "PrinterTool",
    "QueueJobCreate",
    "QueueJobUpdate",
    "RoutingStrategy",
    "Session",
    "TestClient",
    "User",
    "_gcode",
    "_requirements",
    "_user",
    "datetime",
    "fleet",
    "materials",
    "pytest",
    "select",
    "timezone",
]
