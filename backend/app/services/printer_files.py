"""Sync and correlate remote printer G-code inventory."""

from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath
import re
from typing import Any

from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import File, FileType, PrintJob, PrinterFile

_VAULT_MARKER_RE = re.compile(
    r"(?:^|__)vault-f(?P<file_id>\d+)-(?P<sha>[a-fA-F0-9]{8,64})(?:\.|$|[-_])"
)


def _remote_name(raw: dict[str, Any]) -> str | None:
    value = raw.get("path") or raw.get("filename") or raw.get("name")
    if not value:
        return None
    return str(value).strip().lstrip("/")


def _remote_size(raw: dict[str, Any]) -> int | None:
    value = raw.get("size") or raw.get("size_bytes")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _remote_modified(raw: dict[str, Any]) -> datetime | None:
    value = raw.get("modified") or raw.get("modified_at")
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value))
    except (TypeError, ValueError, OSError):
        return None


def build_traceable_remote_filename(file: File) -> str:
    """Return a Moonraker-safe filename with a Vault revision marker."""
    suffix = PurePosixPath(file.original_filename).suffix
    if suffix.lower() not in {".gcode", ".g", ".gco"}:
        suffix = ".gcode"
    stem = PurePosixPath(file.original_filename).stem or "print"
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-_") or "print"
    marker = f"vault-f{file.id}-{file.sha256[:12]}"
    max_stem_len = max(1, 512 - len(marker) - len(suffix) - 2)
    return f"{safe_stem[:max_stem_len]}__{marker}{suffix}"


def _find_marker_match(
    session: Session,
    remote_filename: str,
) -> tuple[int | None, str] | None:
    match = _VAULT_MARKER_RE.search(PurePosixPath(remote_filename).name)
    if not match:
        return None
    file_id = int(match.group("file_id"))
    sha_prefix = match.group("sha").lower()
    file_row = session.get(File, file_id)
    if (
        file_row is not None
        and file_row.file_type == FileType.GCODE
        and file_row.deleted_at is None
        and file_row.sha256.lower().startswith(sha_prefix)
    ):
        return file_id, "vault_marker"
    return None, "vault_marker_mismatch"


def _find_match(
    session: Session,
    printer_id: int,
    remote_filename: str,
    size_bytes: int | None,
) -> tuple[int | None, str]:
    marker_match = _find_marker_match(session, remote_filename)
    if marker_match is not None:
        return marker_match

    job = session.exec(
        select(PrintJob)
        .where(
            PrintJob.printer_id == printer_id,
            PrintJob.remote_filename == remote_filename,
            PrintJob.source == "vault",
        )
        .order_by(PrintJob.created_at.desc())  # type: ignore[attr-defined]
    ).first()
    if job is not None:
        return job.file_id, "upload_history"

    basename = PurePosixPath(remote_filename).name
    file_by_name = session.exec(
        select(File)
        .where(
            File.file_type == FileType.GCODE,
            File.original_filename == basename,
            File.deleted_at.is_(None),  # type: ignore[union-attr]
        )
        .order_by(File.uploaded_at.desc())  # type: ignore[attr-defined]
    ).first()
    if file_by_name is not None and file_by_name.id is not None:
        return file_by_name.id, "filename"

    if size_bytes is not None:
        file_by_size = session.exec(
            select(File)
            .where(
                File.file_type == FileType.GCODE,
                File.size_bytes == size_bytes,
                File.deleted_at.is_(None),  # type: ignore[union-attr]
            )
            .order_by(File.uploaded_at.desc())  # type: ignore[attr-defined]
        ).first()
        if file_by_size is not None and file_by_size.id is not None:
            return file_by_size.id, "size"

    return None, "external"


def upsert_printer_file(
    session: Session,
    *,
    printer_id: int,
    remote_filename: str,
    file_id: int | None = None,
    size_bytes: int | None = None,
    sha256: str | None = None,
    matched_by: str | None = None,
    modified_at: datetime | None = None,
) -> PrinterFile:
    now = utcnow()
    row = session.exec(
        select(PrinterFile).where(
            PrinterFile.printer_id == printer_id,
            PrinterFile.remote_filename == remote_filename,
        )
    ).first()
    if row is None:
        row = PrinterFile(printer_id=printer_id, remote_filename=remote_filename)

    if file_id is None and matched_by is None:
        file_id, matched_by = _find_match(
            session, printer_id, remote_filename, size_bytes
        )

    row.file_id = file_id
    row.size_bytes = size_bytes
    row.sha256 = sha256
    row.matched_by = matched_by or ("upload_history" if file_id else "external")
    row.modified_at = modified_at
    row.last_seen_at = now
    row.missing_since = None
    row.updated_at = now
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def sync_printer_files(
    session: Session,
    *,
    printer_id: int,
    remote_files: list[dict[str, Any]],
) -> list[PrinterFile]:
    seen: set[str] = set()
    synced: list[PrinterFile] = []
    for raw in remote_files:
        remote_filename = _remote_name(raw)
        if not remote_filename:
            continue
        size_bytes = _remote_size(raw)
        row = upsert_printer_file(
            session,
            printer_id=printer_id,
            remote_filename=remote_filename,
            size_bytes=size_bytes,
            sha256=raw.get("sha256") or raw.get("hash"),
            modified_at=_remote_modified(raw),
        )
        seen.add(remote_filename)
        synced.append(row)

    now = utcnow()
    existing = session.exec(
        select(PrinterFile).where(
            PrinterFile.printer_id == printer_id,
            PrinterFile.missing_since.is_(None),  # type: ignore[union-attr]
        )
    ).all()
    for row in existing:
        if row.remote_filename not in seen:
            row.missing_since = now
            row.updated_at = now
            session.add(row)
    session.commit()
    return list_printer_files(session, printer_id=printer_id)


def list_printer_files(session: Session, *, printer_id: int) -> list[PrinterFile]:
    return list(
        session.exec(
            select(PrinterFile)
            .where(PrinterFile.printer_id == printer_id)
            .order_by(PrinterFile.remote_filename.asc())  # type: ignore[attr-defined]
        ).all()
    )
