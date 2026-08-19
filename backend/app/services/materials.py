from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    ArtifactMaterialRequirement,
    File,
    MaterialSlotState,
    MaterialSource,
    Metadata,
    Printer,
    PrinterMaterialSlot,
    PrinterStatus,
    PrinterTool,
    User,
)
from app.schemas.materials import (
    ArtifactRequirementRead,
    CompatibilityPrinterRead,
    CompatibilityRead,
    ManualMaterialStateUpdate,
    MaterialSlotRead,
    MaterialToolRead,
    PrinterMaterialStateRead,
)


class MaterialStateError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _provider_stale(printer: Printer, observed_at) -> bool:
    return (
        observed_at is None
        or printer.status in {PrinterStatus.UNKNOWN, PrinterStatus.OFFLINE}
        or not printer.provider_material_sync_enabled
    )


def _confidence(source: MaterialSource) -> str:
    if source == MaterialSource.BAMBU_AMS:
        return "provider_reported"
    if source == MaterialSource.MOONRAKER_SPOOLMAN:
        return "externally_tracked"
    return "operator_set"


def ensure_default_tool(session: Session, printer: Printer) -> None:
    if printer.id is None:
        return
    existing = session.exec(
        select(PrinterTool).where(
            PrinterTool.printer_id == printer.id,
            PrinterTool.tool_key == "tool0",
            PrinterTool.source == MaterialSource.MANUAL,
        )
    ).first()
    if existing is None:
        session.add(
            PrinterTool(
                printer_id=printer.id,
                tool_key="tool0",
                label="Tool 0",
                source=MaterialSource.MANUAL,
            )
        )


def read_material_state(session: Session, printer_id: int) -> PrinterMaterialStateRead:
    printer = session.get(Printer, printer_id)
    if printer is None or printer.deleted_at is not None:
        raise MaterialStateError("printer_not_found")
    ensure_default_tool(session, printer)
    session.flush()
    tools = list(
        session.exec(
            select(PrinterTool)
            .where(PrinterTool.printer_id == printer_id)
            .order_by(PrinterTool.tool_key)
        ).all()
    )
    rows = list(
        session.exec(
            select(PrinterMaterialSlot)
            .where(PrinterMaterialSlot.printer_id == printer_id)
            .order_by(PrinterMaterialSlot.source, PrinterMaterialSlot.slot_key)
        ).all()
    )
    active_provider_keys = {
        row.slot_key
        for row in rows
        if row.source != MaterialSource.MANUAL
        and not _provider_stale(printer, row.observed_at)
    }
    active_provider_tool_keys = {
        row.tool_key
        for row in tools
        if row.source != MaterialSource.MANUAL
        and not _provider_stale(printer, row.observed_at)
    }
    visible_tools = [
        row
        for row in tools
        if row.source != MaterialSource.MANUAL
        or row.tool_key not in active_provider_tool_keys
    ]
    visible = [
        row
        for row in rows
        if row.source != MaterialSource.MANUAL
        or row.slot_key not in active_provider_keys
    ]
    return PrinterMaterialStateRead(
        printer_id=printer_id,
        updated_at=printer.updated_at,
        provider_sync_enabled=printer.provider_material_sync_enabled,
        tools=[
            MaterialToolRead(
                tool_key=row.tool_key,
                label=row.label,
                nozzle_diameter_mm=row.nozzle_diameter_mm,
                source=row.source,
                observed_at=row.observed_at,
                stale=row.source != MaterialSource.MANUAL
                and _provider_stale(printer, row.observed_at),
            )
            for row in visible_tools
        ],
        slots=[
            MaterialSlotRead(
                slot_key=row.slot_key,
                label=row.label,
                tool_key=row.tool_key,
                state=row.state,
                source=row.source,
                confidence=_confidence(row.source),  # type: ignore[arg-type]
                material_type=row.material_type,
                material_brand=row.material_brand,
                color_hex=row.color_hex,
                spool_id=row.spool_id,
                spool_name=row.spool_name,
                spool_filament_id=row.spool_filament_id,
                observed_at=row.observed_at,
                stale=row.source != MaterialSource.MANUAL
                and _provider_stale(printer, row.observed_at),
            )
            for row in visible
        ],
    )


def replace_manual_state(
    session: Session,
    printer_id: int,
    payload: ManualMaterialStateUpdate,
    current_user: User,
) -> PrinterMaterialStateRead:
    printer = session.get(Printer, printer_id)
    if printer is None or printer.deleted_at is not None:
        raise MaterialStateError("printer_not_found")
    if (
        payload.expected_updated_at
        and printer.updated_at != payload.expected_updated_at
    ):
        raise MaterialStateError("material_state_changed")
    tool_keys = [row.tool_key for row in payload.tools]
    slot_keys = [row.slot_key for row in payload.slots]
    if len(tool_keys) != len(set(tool_keys)) or len(slot_keys) != len(set(slot_keys)):
        raise MaterialStateError("material_slot_duplicate")
    known_tools = set(tool_keys)
    if any(
        row.tool_key is not None and row.tool_key not in known_tools
        for row in payload.slots
    ):
        raise MaterialStateError("material_slot_tool_unknown")
    for row in payload.slots:
        if row.state == MaterialSlotState.LOADED and not row.material_type:
            raise MaterialStateError("loaded_material_type_required")

    for row in session.exec(
        select(PrinterTool).where(
            PrinterTool.printer_id == printer_id,
            PrinterTool.source == MaterialSource.MANUAL,
        )
    ).all():
        session.delete(row)
    for row in session.exec(
        select(PrinterMaterialSlot).where(
            PrinterMaterialSlot.printer_id == printer_id,
            PrinterMaterialSlot.source == MaterialSource.MANUAL,
        )
    ).all():
        session.delete(row)
    now = utcnow()
    for item in payload.tools:
        session.add(
            PrinterTool(
                printer_id=printer_id,
                tool_key=item.tool_key.strip(),
                label=item.label.strip(),
                nozzle_diameter_mm=item.nozzle_diameter_mm,
                source=MaterialSource.MANUAL,
                created_by=current_user.id,
                updated_by=current_user.id,
                created_at=now,
                updated_at=now,
            )
        )
    for item in payload.slots:
        session.add(
            PrinterMaterialSlot(
                printer_id=printer_id,
                slot_key=item.slot_key.strip(),
                label=item.label.strip(),
                tool_key=item.tool_key,
                state=item.state,
                source=MaterialSource.MANUAL,
                material_type=item.material_type.strip()
                if item.material_type
                else None,
                material_brand=item.material_brand.strip()
                if item.material_brand
                else None,
                color_hex=item.color_hex,
                spool_id=item.spool_id,
                spool_name=item.spool_name,
                spool_filament_id=item.spool_filament_id,
                observed_at=now,
                created_by=current_user.id,
                updated_by=current_user.id,
                created_at=now,
                updated_at=now,
            )
        )
    printer.updated_by = current_user.id
    printer.updated_at = now
    session.add(printer)
    session.commit()
    return read_material_state(session, printer_id)


@dataclass(frozen=True)
class CompatibilityResult:
    verdict: str
    reasons: tuple[str, ...]
    missing_materials: tuple[str, ...] = ()
    color_advisories: tuple[str, ...] = ()


def _normalise(value: str) -> str:
    return value.strip().casefold()


def compatibility_for_printer(
    session: Session, file_id: int, printer_id: int
) -> CompatibilityResult:
    artifact = session.get(File, file_id)
    printer = session.get(Printer, printer_id)
    if artifact is None or artifact.deleted_at is not None:
        raise MaterialStateError("file_not_found")
    if printer is None or printer.deleted_at is not None:
        raise MaterialStateError("printer_not_found")
    requirements = list(
        session.exec(
            select(ArtifactMaterialRequirement).where(
                ArtifactMaterialRequirement.file_id == file_id
            )
        ).all()
    )
    metadata = session.exec(select(Metadata).where(Metadata.file_id == file_id)).first()
    required_types = {
        _normalise(row.material_type)
        for row in requirements
        if row.material_type and row.material_type.strip()
    }
    nozzle = metadata.nozzle_diameter_mm if metadata else None
    if not required_types and nozzle is None:
        return CompatibilityResult("unknown", ("job_material_unknown",))

    state = read_material_state(session, printer_id)
    loaded = [
        row
        for row in state.slots
        if not row.stale and row.state == MaterialSlotState.LOADED
    ]
    explicit_slots = [
        row
        for row in state.slots
        if not row.stale and row.state != MaterialSlotState.UNKNOWN
    ]
    unknown_slots = [
        row
        for row in state.slots
        if not row.stale and row.state == MaterialSlotState.UNKNOWN
    ]
    incomplete_loaded = [row for row in loaded if not row.material_type]
    tools = [row for row in state.tools if not row.stale]
    if not explicit_slots and not any(
        row.nozzle_diameter_mm is not None for row in tools
    ):
        return CompatibilityResult("unknown", ("printer_material_unknown",))

    loaded_types = {
        _normalise(row.material_type)
        for row in loaded
        if row.material_type and row.material_type.strip()
    }
    missing = sorted(required_types - loaded_types)
    reasons: list[str] = []
    if missing and explicit_slots and not unknown_slots and not incomplete_loaded:
        reasons.append("material_type_mismatch")
    elif missing:
        reasons.append("printer_material_incomplete")
    known_requirements = [row for row in requirements if row.material_type]
    if len(known_requirements) > 1:
        mapped_tools = {row.tool_key for row in loaded if row.tool_key}
        required_tools = {f"tool{row.tool_index}" for row in known_requirements}
        if not required_tools.issubset(mapped_tools):
            reasons.append("tool_feed_mapping_unknown")
    if nozzle is not None:
        known_nozzles = [
            row.nozzle_diameter_mm
            for row in tools
            if row.nozzle_diameter_mm is not None
        ]
        if known_nozzles and not any(
            abs(value - nozzle) <= 0.01 for value in known_nozzles
        ):
            reasons.append("nozzle_diameter_mismatch")
        elif not known_nozzles:
            reasons.append("printer_nozzle_unknown")

    colors_by_type = {
        _normalise(row.material_type): row.color_hex
        for row in loaded
        if row.material_type and row.color_hex
    }
    color_advisories = sorted(
        {
            f"tool{row.tool_index}:{row.color_hex}->{colors_by_type[_normalise(row.material_type)]}"
            for row in requirements
            if row.material_type
            and row.color_hex
            and _normalise(row.material_type) in colors_by_type
            and colors_by_type[_normalise(row.material_type)] != row.color_hex
        }
    )
    mismatch_reasons = [reason for reason in reasons if reason.endswith("mismatch")]
    if mismatch_reasons:
        return CompatibilityResult(
            "mismatch", tuple(reasons), tuple(missing), tuple(color_advisories)
        )
    if reasons or (required_types and not explicit_slots):
        return CompatibilityResult(
            "unknown",
            tuple(reasons or ["printer_material_unknown"]),
            tuple(missing),
            tuple(color_advisories),
        )
    return CompatibilityResult("compatible", (), (), tuple(color_advisories))


def compatibility_report(
    session: Session, file_id: int, printer_ids: list[int]
) -> CompatibilityRead:
    requirements = list(
        session.exec(
            select(ArtifactMaterialRequirement)
            .where(ArtifactMaterialRequirement.file_id == file_id)
            .order_by(ArtifactMaterialRequirement.tool_index)
        ).all()
    )
    metadata = session.exec(select(Metadata).where(Metadata.file_id == file_id)).first()
    return CompatibilityRead(
        file_id=file_id,
        requirements=[
            ArtifactRequirementRead(
                tool_index=row.tool_index,
                material_type=row.material_type,
                color_hex=row.color_hex,
            )
            for row in requirements
        ],
        nozzle_diameter_mm=metadata.nozzle_diameter_mm if metadata else None,
        printers=[
            CompatibilityPrinterRead(
                printer_id=printer_id,
                verdict=(
                    result := compatibility_for_printer(session, file_id, printer_id)
                ).verdict,  # type: ignore[arg-type]
                reasons=list(result.reasons),
                missing_materials=list(result.missing_materials),
                color_advisories=list(result.color_advisories),
            )
            for printer_id in printer_ids
        ],
    )
