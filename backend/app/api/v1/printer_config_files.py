"""Administrator access to existing source files in Moonraker's config root."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from printstash_core.printers import MoonrakerConfig
from pydantic import ValidationError
from sqlmodel import Session

from app.core.http import get_or_404
from app.core.security import require_auth, require_user
from app.db.models import Printer, PrinterProvider, PrinterRole, User
from app.db.session import get_session
from app.modules.identity.printer_rbac import require_printer_role
from app.modules.printing.printer_config_files import (
    ConfigFileError,
    ConfigFileProblem,
    PrinterConfigFilesClient,
)
from app.schemas.printer_config_files import (
    MAX_CONFIG_REQUEST_BYTES,
    PrinterConfigFile,
    PrinterConfigFiles,
    PrinterConfigFileUpdate,
)

router = APIRouter(prefix="/printers/{printer_id}/config", tags=["printers"])

_STATUS = {
    ConfigFileProblem.INVALID_PATH: 422,
    ConfigFileProblem.NOT_FOUND: 404,
    ConfigFileProblem.READ_ONLY: 409,
    ConfigFileProblem.TOO_LARGE: 413,
    ConfigFileProblem.NOT_TEXT: 422,
    ConfigFileProblem.CONFLICT: 409,
    ConfigFileProblem.UNAVAILABLE: 502,
    ConfigFileProblem.OUTCOME_UNKNOWN: 502,
}


def _client(
    printer_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> PrinterConfigFilesClient:
    require_printer_role(session, current_user, printer_id, PrinterRole.ADMIN)
    printer = get_or_404(session, Printer, printer_id, "printer_not_found")
    if printer.provider != PrinterProvider.MOONRAKER:
        raise HTTPException(409, detail="operation_not_supported_for_provider")
    if not printer.moonraker_url or not printer.moonraker_url.strip():
        raise HTTPException(502, detail="provider_credentials_missing")
    try:
        return PrinterConfigFilesClient(
            MoonrakerConfig(printer.moonraker_url, printer.api_key)
        )
    except ConfigFileError as exc:
        raise HTTPException(_STATUS[exc.problem], detail=exc.problem) from exc


async def _update_body(request: Request) -> PrinterConfigFileUpdate:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_CONFIG_REQUEST_BYTES:
            raise HTTPException(413, detail="request_too_large")
        body.extend(chunk)
    try:
        return PrinterConfigFileUpdate.model_validate_json(body)
    except ValidationError as exc:
        # Configuration files can contain secrets. Never echo invalid input.
        raise HTTPException(422, detail="request_validation_failed") from exc


@router.get("/files", summary="List existing Moonraker configuration files")
async def list_printer_config_files(
    response: Response,
    client: PrinterConfigFilesClient = Depends(_client),
) -> PrinterConfigFiles:
    response.headers["Cache-Control"] = "no-store"
    try:
        return PrinterConfigFiles(files=await client.list_files())
    except ConfigFileError as exc:
        raise HTTPException(_STATUS[exc.problem], detail=exc.problem) from exc


@router.get("/file", summary="Read an existing Moonraker configuration file")
async def read_printer_config_file(
    response: Response,
    path: str = Query(min_length=1, max_length=512),
    client: PrinterConfigFilesClient = Depends(_client),
) -> PrinterConfigFile:
    response.headers["Cache-Control"] = "no-store"
    try:
        return await client.read(path)
    except ConfigFileError as exc:
        raise HTTPException(_STATUS[exc.problem], detail=exc.problem) from exc


@router.put(
    "/file",
    summary="Save an existing Moonraker configuration file and verify readback",
    dependencies=[Depends(require_auth)],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": PrinterConfigFileUpdate.model_json_schema()
                },
            },
        }
    },
)
async def save_printer_config_file(
    request: Request,
    response: Response,
    path: str = Query(min_length=1, max_length=512),
    client: PrinterConfigFilesClient = Depends(_client),
) -> PrinterConfigFile:
    response.headers["Cache-Control"] = "no-store"
    payload = await _update_body(request)
    try:
        return await client.save(path, payload.content, payload.base_revision)
    except ConfigFileError as exc:
        raise HTTPException(_STATUS[exc.problem], detail=exc.problem) from exc
