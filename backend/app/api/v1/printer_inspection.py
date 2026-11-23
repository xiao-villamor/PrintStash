"""Printer workbench inspection and bed measurement."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from printstash_core.printers import (
    MachineControlError,
    ProviderError,
    ProviderRegistry,
)
from sqlmodel import Session

from app.core.http import get_or_404
from app.core.security import require_auth, require_user
from app.db.models import Printer, PrinterProvider, PrinterRole, User
from app.db.session import get_session
from app.modules.identity.printer_rbac import require_printer_role
from app.modules.printing.printer_inspection import (
    InvalidInspectionResponse,
    read_bed_mesh,
    read_heater_history,
)
from app.modules.printing.printer_provider import MoonrakerProvider, get_provider_client
from app.schemas.printer_inspection import (
    ActiveBedMesh,
    BedMeasurementAvailability,
    ConfirmBedMeasurement,
    PrinterBedMesh,
    PrinterHeaterHistory,
)

router = APIRouter(prefix="/printers", tags=["printers"])
_INSPECTION_TIMEOUT_SECONDS = 5
_Result = TypeVar("_Result")


def _inspection_provider(
    printer_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MoonrakerProvider:
    require_printer_role(session, current_user, printer_id, PrinterRole.VIEW)
    return _moonraker_provider(printer_id, request, session)


def _bed_measurement_provider(
    printer_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> MoonrakerProvider:
    require_printer_role(session, current_user, printer_id, PrinterRole.CONTROL)
    return _moonraker_provider(printer_id, request, session)


def _moonraker_provider(
    printer_id: int, request: Request, session: Session
) -> MoonrakerProvider:
    printer = get_or_404(session, Printer, printer_id, "printer_not_found")
    if printer.provider != PrinterProvider.MOONRAKER:
        raise HTTPException(409, detail="operation_not_supported_for_provider")
    registry: ProviderRegistry = request.app.state.printer_provider_registry
    try:
        provider = get_provider_client(printer, registry=registry)
    except ProviderError as exc:
        raise HTTPException(502, detail=exc.code) from exc
    if not isinstance(provider, MoonrakerProvider):
        raise HTTPException(502, detail="provider_config_mismatch")
    return provider


async def _read(
    fetch: Callable[[], Awaitable[object]], parse: Callable[[object], _Result]
) -> _Result:
    try:
        async with asyncio.timeout(_INSPECTION_TIMEOUT_SECONDS):
            data = await fetch()
        return parse(data)
    except InvalidInspectionResponse as exc:
        raise HTTPException(502, detail="printer_inspection_invalid_response") from exc
    except (ProviderError, TimeoutError) as exc:
        raise HTTPException(502, detail="printer_inspection_unreachable") from exc


@router.get(
    "/{printer_id}/heater-history", summary="Read Moonraker's recent heater samples"
)
async def printer_heater_history(
    response: Response, provider: MoonrakerProvider = Depends(_inspection_provider)
) -> PrinterHeaterHistory:
    response.headers["Cache-Control"] = "no-store"
    return await _read(provider.temperature_store, read_heater_history)


@router.get("/{printer_id}/bed-mesh", summary="Read Klipper's active bed mesh")
async def printer_bed_mesh(
    response: Response, provider: MoonrakerProvider = Depends(_inspection_provider)
) -> PrinterBedMesh:
    response.headers["Cache-Control"] = "no-store"
    return await _read(provider.bed_mesh_status, read_bed_mesh)


@router.get(
    "/{printer_id}/bed-mesh/measurement", summary="Check for automatic bed measurement"
)
async def printer_bed_measurement_availability(
    response: Response, provider: MoonrakerProvider = Depends(_bed_measurement_provider)
) -> BedMeasurementAvailability:
    response.headers["Cache-Control"] = "no-store"
    try:
        async with asyncio.timeout(_INSPECTION_TIMEOUT_SECONDS):
            supported = await provider.bed_measurement_supported()
    except (ProviderError, TimeoutError) as exc:
        raise HTTPException(502, detail="printer_inspection_unreachable") from exc
    return BedMeasurementAvailability(supported=supported)


@router.post(
    "/{printer_id}/bed-mesh/measurement",
    summary="Measure the bed with Klipper's automatic probe",
    dependencies=[Depends(require_auth)],
)
async def measure_printer_bed(
    payload: ConfirmBedMeasurement,
    response: Response,
    provider: MoonrakerProvider = Depends(_bed_measurement_provider),
) -> ActiveBedMesh:
    response.headers["Cache-Control"] = "no-store"
    try:
        mesh = read_bed_mesh(await provider.measure_bed())
    except MachineControlError as exc:
        raise HTTPException(409, detail=exc.reason.value) from exc
    except ProviderError as exc:
        raise HTTPException(502, detail=exc.code) from exc
    except InvalidInspectionResponse as exc:
        raise HTTPException(502, detail="bed_measurement_mesh_unavailable") from exc
    if not isinstance(mesh, ActiveBedMesh):
        raise HTTPException(502, detail="bed_measurement_mesh_unavailable")
    return mesh
