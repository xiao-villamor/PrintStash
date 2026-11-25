"""Immediate printer dispatch with explicit product dependencies.

Authorization and artifact compatibility precede provider I/O. The persisted
UPLOADING job, terminal transfer outcome and inventory registration retain their
separate durable phases; SQL cannot roll back an accepted printer command.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from printstash_core.gcode import declared_print_artifact_format
from sqlalchemy import update
from sqlmodel import Session

from app.core.errors import ErrorKind, OperationError
from app.core.logging import get_logger
from app.core.time import utcnow
from app.db.models import (
    CollectionRole,
    CompatibilityPolicy,
    File,
    FileType,
    Model,
    Printer,
    PrinterProvider,
    PrinterRole,
    PrintJob,
    PrintJobState,
    User,
)
from app.modules.identity import printer_rbac, rbac
from app.modules.media.sliced_gcode import selected_plate
from app.modules.printing import materials, production_costing
from app.modules.printing.printer_files import (
    build_traceable_remote_filename,
    upsert_printer_file,
)
from app.modules.printing.printer_jobs import (
    PrinterJobError,
    project_subtask_id,
    reproducibility_payload,
    transfer_artifact,
)
from app.modules.printing.printer_provider import (
    PrinterProviderClient,
    ProviderError,
)
from app.modules.storage.storage_backend.contracts import StorageBackend
from app.schemas.printers import (
    PrintJobRead,
    SendToPrinter,
)

logger = get_logger(__name__)


async def require_ready_before_send(provider: PrinterProviderClient) -> None:
    if provider.capabilities.requires_ready_before_send:
        try:
            status = await provider.query_status()
        except ProviderError as exc:
            raise OperationError(exc.code, kind=ErrorKind.UPSTREAM) from exc
        state = str(
            status.get("result", {})
            .get("status", {})
            .get("print_stats", {})
            .get("state", "")
        ).lower()
        if state not in {"standby", "ready", "idle", "complete", "cancelled"}:
            raise OperationError("printer_not_ready", kind=ErrorKind.CONFLICT)


async def send_to_printer(
    printer_id: int,
    payload: SendToPrinter,
    current_user: User,
    session: Session,
    *,
    provider_builder: Callable[[Printer], PrinterProviderClient],
    backend_provider: Callable[[], StorageBackend],
) -> PrintJobRead:
    _require_printer_role(session, current_user, printer_id, PrinterRole.PRINT)
    p = _require_row(session, Printer, printer_id, "printer_not_found")
    provider = provider_builder(p)
    if not provider.capabilities.can_upload:
        raise OperationError(
            "operation_not_supported_for_provider", kind=ErrorKind.CONFLICT
        )
    # Resolve and authorize the artifact before any provider I/O. Besides avoiding
    # unnecessary traffic, this guarantees unsupported/corrupt requests cannot
    # disclose a printer's live state or wake a device on the local network.
    f = _require_row(session, File, payload.file_id, "file_not_found")
    if f.file_type != FileType.GCODE:
        raise OperationError("file_not_gcode", kind=ErrorKind.INVALID)
    artifact_format = declared_print_artifact_format(f.original_filename)
    if not provider.capabilities.accepts_format(artifact_format):
        raise OperationError("artifact_format_not_supported", kind=ErrorKind.CONFLICT)
    _require_file_role(session, current_user, f, CollectionRole.EDIT)
    plate_index = await asyncio.to_thread(selected_plate, f, payload.plate_index)
    await require_ready_before_send(provider)
    if payload.start_print:
        try:
            compatibility = materials.compatibility_for_printer(
                session, int(f.id), printer_id
            )
        except materials.MaterialStateError as exc:
            raise OperationError(exc.code, kind=ErrorKind.NOT_FOUND) from exc
        if (
            compatibility.verdict == "mismatch"
            and payload.compatibility_policy == CompatibilityPolicy.SAFE
        ):
            raise OperationError(
                "material_mismatch_confirmation_required", kind=ErrorKind.CONFLICT
            )
    backend = backend_provider()
    blob_exists = await asyncio.to_thread(backend.exists, f.path)
    if not blob_exists:
        raise OperationError("file_blob_missing", kind=ErrorKind.GONE)
    native_project = plate_index is not None and p.provider == PrinterProvider.BAMBU_LAN
    remote_name = (
        payload.remote_filename.strip()
        if payload.remote_filename
        else build_traceable_remote_filename(
            f, plate_index=plate_index, native_project=native_project
        )
    )
    if native_project:
        if not remote_name.lower().endswith(".3mf"):
            remote_name += ".3mf"
    elif artifact_format.value == "bgcode_binary":
        if not remote_name.lower().endswith(".bgcode"):
            remote_name += ".bgcode"
    elif not remote_name.lower().endswith((".gcode", ".g", ".gco")):
        remote_name += ".gcode"

    job = PrintJob(
        printer_id=printer_id,
        file_id=f.id,  # type: ignore[arg-type]
        model_id=f.model_id,
        remote_filename=remote_name,
        state=PrintJobState.UPLOADING,
        provider_job_id=project_subtask_id()
        if native_project and payload.start_print
        else None,
        plate_index=plate_index,
        spool_id=payload.spool_id,
        spool_name=payload.spool_name,
        spool_filament_id=payload.spool_filament_id,
        compatibility_policy=payload.compatibility_policy,
        material_override_by=(
            current_user.id
            if payload.compatibility_policy == CompatibilityPolicy.ALLOW_MISMATCH
            else None
        ),
        material_override_at=(
            utcnow()
            if payload.compatibility_policy == CompatibilityPolicy.ALLOW_MISMATCH
            else None
        ),
    )
    if payload.start_print:
        production_costing.launch_job_cost(session, p, job, f)
    session.add(job)
    session.commit()
    session.refresh(job)

    try:
        receipt = await transfer_artifact(
            backend,
            provider,
            f,
            remote_name,
            start_print=payload.start_print,
            subtask_id=job.provider_job_id,
            plate_index=plate_index,
        )
    except ProviderError as exc:
        job.state = PrintJobState.FAILED
        job.error = _provider_action_code(exc)
        job.finished_at = utcnow()
        job.production_cost = None
        session.add(job)
        session.commit()
        raise OperationError(
            _provider_action_code(exc), kind=ErrorKind.UPSTREAM
        ) from exc
    except PrinterJobError as exc:
        job.state = PrintJobState.FAILED
        job.error = exc.code
        job.finished_at = utcnow()
        job.production_cost = None
        session.add(job)
        session.commit()
        error_kind = {
            "invalid_binary_gcode": ErrorKind.INVALID,
            "print_artifact_extension_mismatch": ErrorKind.INVALID,
            "artifact_format_not_supported": ErrorKind.CONFLICT,
            "file_blob_missing": ErrorKind.GONE,
        }.get(exc.code, ErrorKind.UPSTREAM)
        raise OperationError(exc.code, kind=error_kind) from exc
    except OperationError:
        raise
    except Exception as exc:
        logger.error("send_to_printer failed printer=%s file=%s", printer_id, f.id)
        job.state = PrintJobState.FAILED
        job.error = "provider_error"
        job.finished_at = utcnow()
        job.production_cost = None
        session.add(job)
        session.commit()
        raise OperationError("provider_error", kind=ErrorKind.UPSTREAM) from exc
    # Upload-only is not a queued print. It only records transfer history; user
    # can start the remote file later from printer inventory.
    now = utcnow()
    session.exec(
        update(PrintJob)
        .where(PrintJob.id == job.id, PrintJob.state == PrintJobState.UPLOADING)
        .values(
            state=PrintJobState.STARTED
            if payload.start_print
            else PrintJobState.COMPLETED,
            started_at=now if payload.start_print else None,
            finished_at=None if payload.start_print else now,
            updated_at=now,
        )
    )
    session.commit()
    session.refresh(job)
    out = PrintJobRead(
        **job.model_dump(
            exclude={"artifact_capture_error_code", "artifact_capture_error_message"}
        ),
        **reproducibility_payload(
            job,
            file_type=f.file_type,
            download_url=(
                f"/api/v1/files/{job.file_id}/download"
                if job.source == "vault" or job.artifact_evidence.endswith("_archived")
                else None
            ),
        ),
    )
    upsert_printer_file(
        session,
        printer_id=printer_id,
        file_id=f.id,  # type: ignore[arg-type]
        remote_filename=remote_name,
        size_bytes=receipt.size_bytes,
        sha256=receipt.sha256,
        matched_by="upload_history",
        plate_index=plate_index,
    )
    return out


def _provider_action_code(exc: ProviderError) -> str:
    """Return the precise persisted reason when a provider supplies one."""

    return str(getattr(exc, "action_code", None) or exc.code)


def _require_row(session, model_type, identity, code):
    row = session.get(model_type, identity)
    if row is None:
        raise OperationError(code, kind=ErrorKind.NOT_FOUND)
    return row


def _require_printer_role(session, actor, printer_id, minimum):
    if not actor.is_active:
        raise OperationError("printer_permission_denied", kind=ErrorKind.FORBIDDEN)
    printer = _require_row(session, Printer, printer_id, "printer_not_found")
    if printer.deleted_at is not None:
        raise OperationError("printer_not_found", kind=ErrorKind.NOT_FOUND)
    role = printer_rbac.effective_printer_role(session, actor, printer_id)
    if not printer_rbac.role_allows(role, minimum):
        raise OperationError("printer_permission_denied", kind=ErrorKind.FORBIDDEN)


def _require_file_role(session, actor, file_row, minimum):
    model = _require_row(session, Model, file_row.model_id, "file_not_found")
    if model.deleted_at is not None:
        raise OperationError("file_not_found", kind=ErrorKind.NOT_FOUND)
    if model.collection_id is None and not actor.is_superuser:
        raise OperationError("root_collection_admin_required", kind=ErrorKind.FORBIDDEN)
    role = rbac.effective_collection_role(session, actor, model.collection_id)
    if not rbac.role_allows(role, minimum):
        raise OperationError("collection_permission_denied", kind=ErrorKind.FORBIDDEN)
