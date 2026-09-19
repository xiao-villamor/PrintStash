"""Mounted and remote library-source scan and reconciliation engine.

The configured source is the source of truth. Mounted sources may accept
create-only write-back. S3, WebDAV, and SFTP sources are read-only: scans page
through their namespace and materialize only the objects that need indexing or
verification. PrintStash never overwrites or deletes source bytes.

Safety: a scan never mass-deletes on an unmounted/empty root. If ``root_path`` is
missing/unreadable, or it yields zero candidate files while the library still has
live indexed files, the scan aborts with an error and changes nothing.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import stat
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Optional

from croniter import croniter
from sqlalchemy import exists, func, or_, update
from sqlmodel import Session, select

from app.core.logging import get_logger
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    SUFFIX_TO_FILE_TYPE,
    ExternalLibrary,
    ExternalLibraryCheckpoint,
    ExternalLibraryCollectionMode,
    ExternalLibraryObservation,
    ExternalLibraryScanStatus,
    ExternalLibraryTombstone,
    ExternalLibraryWatchMode,
    File,
    FileType,
    LibrarySourceKind,
    Metadata,
    Model,
)
from app.db.projections import content_changed
from app.db.scopes import live
from app.db.session import SessionFactory, get_session_factory
from app.modules.ingestion.ingestion import (
    persist_artifact,
    resolve_or_create_model,
    strategy_for_artifact,
)
from app.modules.library import taxonomy
from app.modules.sources.library_source import (
    LibrarySource,
    LibrarySourceError,
    SourceEntry,
    source_for_library,
)
from app.modules.sources.library_source import (
    timestamp as source_timestamp,
)
from app.modules.storage.filesystem import FsKind, detect_fs_kind
from app.modules.storage.hashing import sha256_file
from app.modules.storage.root_markers import (
    read_root_marker_fd,
)
from app.runtime.jobs import registry

from .root_binding import (
    ExternalRootBindingError,
    assert_root_binding,
    expected_root_marker,
)

logger = get_logger(__name__)

# Filesystem mtime granularity varies wildly (FAT rounds to 2 s, SMB/CIFS round,
# floats lose precision on round-trip), so the cheap "unchanged" skip needs real
# slack — 1e-6 absorbed nothing and forced a full sha256 re-hash of every file
# with sub-second mtime jitter on each scan. 2 s covers the worst case (FAT);
# the hash compare in _reindex_changed still catches any genuine edit on the
# next size change, so this only trades a re-hash storm for the cheap skip.
_MTIME_TOLERANCE_S = 2.0
_PROGRESS_FLUSH_INTERVAL_S = 0.25
_PROGRESS_PERCENT_STEP = 1

_PINNED_READ_PATHS: contextvars.ContextVar[dict[str, Path] | None] = (
    contextvars.ContextVar("external_library_pinned_read_paths", default=None)
)
_REMOTE_SCAN_LOCK = threading.Lock()
_REMOTE_PAGE_LIMIT = 1000
_REMOTE_SLICE_MAX_BYTES = 2 * 1024 * 1024 * 1024
_REMOTE_SLICE_MAX_SECONDS = 15 * 60
_SOURCE_HASH_MAX_AGE = timedelta(days=7)


@dataclass
class _ScanProgressCoalescer:
    """Bound progress writes by time or percentage while always flushing final."""

    total: int
    last_flush_at: float = field(default_factory=monotonic)
    last_percent: int = 0

    def should_flush(self, processed: int, *, now: float | None = None) -> bool:
        if self.total <= 0:
            return processed > 0
        current = monotonic() if now is None else now
        percent = min(100, int(processed * 100 / self.total))
        if (
            processed >= self.total
            or percent >= self.last_percent + _PROGRESS_PERCENT_STEP
            or current - self.last_flush_at >= _PROGRESS_FLUSH_INTERVAL_S
        ):
            self.last_percent = percent
            self.last_flush_at = current
            return True
        return False


def should_watch(library: ExternalLibrary, fs_kind: FsKind) -> bool:
    """Whether real-time watching is active for *library* given its watch mode."""
    if not library.enabled:
        return False
    if library.source_kind != LibrarySourceKind.MOUNTED:
        return False
    if library.watch_mode == ExternalLibraryWatchMode.OFF:
        return False
    if library.watch_mode == ExternalLibraryWatchMode.EVENTS:
        return True
    # AUTO: only watch local filesystems.
    return fs_kind == "local"


def is_due(schedule: str, last_scanned_at: Optional[datetime], now: datetime) -> bool:
    """True if a cron *schedule* has fired since *last_scanned_at*.

    Empty/invalid schedules are manual-only and never due. A library that has
    never been scanned is due as soon as it has a valid schedule.
    """
    if not schedule or not croniter.is_valid(schedule):
        return False
    if last_scanned_at is None:
        return True
    base = ensure_utc(last_scanned_at)
    next_fire = croniter(schedule, base).get_next(datetime)
    return next_fire <= now


@dataclass
class ScanSummary:
    added: int = 0
    updated: int = 0
    removed: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    error: Optional[str] = None
    aborted: bool = False

    def as_dict(self) -> dict:
        return {
            "added": self.added,
            "updated": self.updated,
            "removed": self.removed,
            "skipped": self.skipped,
            "errors": self.errors,
            "error": self.error,
            "aborted": self.aborted,
        }


def _strategy_for(file_type: FileType):
    return strategy_for_artifact(file_type)


def _process_external_file(strategy, file_type: FileType, read_path: Path) -> tuple[dict, bytes | None]:
    """Read only the bounded facts required for G-code compatibility."""
    from app.modules.media import gcode_parser

    return (gcode_parser.parse(read_path) if file_type == FileType.GCODE else {}), None


def _walk(root: Path) -> dict[str, tuple[int, float]]:
    """Map supported regular files, aborting on any incomplete traversal.

    A skipped stat/listing error would make a previously indexed path look
    absent and trigger reconciliation. Symlinks are ignored so an external
    root cannot escape its declared boundary.
    """
    disk: dict[str, tuple[int, float]] = {}

    def raise_walk_error(exc: OSError) -> None:
        raise exc

    for directory, dirnames, filenames in os.walk(
        root, topdown=True, followlinks=False, onerror=raise_walk_error
    ):
        base = Path(directory)
        # Never traverse a directory symlink, including implementations that
        # include it in dirnames even with followlinks disabled.
        dirnames[:] = [name for name in dirnames if not (base / name).is_symlink()]
        for filename in filenames:
            path = base / filename
            if path.is_symlink() or path.suffix.lower() not in SUFFIX_TO_FILE_TYPE:
                continue
            st = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode):
                continue
            disk[str(path)] = (st.st_size, st.st_mtime)
    return disk


@dataclass(frozen=True)
class _PinnedFile:
    path: str
    name: str
    parent_fd: int
    size: int
    mtime: float
    device: int
    inode: int


@dataclass
class _PinnedSnapshot:
    files: dict[str, _PinnedFile]
    directory_fds: list[int]

    def close(self) -> None:
        while self.directory_fds:
            os.close(self.directory_fds.pop())


def _walk_pinned(root: Path, expected: dict[str, object]) -> _PinnedSnapshot:
    """Traverse one physical root through directory descriptors.

    The returned paths retain their configured spelling, while all discovery
    and subsequent reads are anchored to the opened directory descriptors.
    """
    root_fd = os.open(
        root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    snapshot = _PinnedSnapshot({}, [root_fd])
    try:
        actual = read_root_marker_fd(root_fd)
        if actual != expected:
            raise ExternalRootBindingError("mismatch", "root_marker_mismatch")

        def visit(directory_fd: int, directory: Path) -> None:
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    name = entry.name
                    if entry.is_symlink():
                        continue
                    path = directory / name
                    if entry.is_dir(follow_symlinks=False):
                        child_fd = os.open(
                            name,
                            os.O_RDONLY
                            | getattr(os, "O_DIRECTORY", 0)
                            | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=directory_fd,
                        )
                        snapshot.directory_fds.append(child_fd)
                        visit(child_fd, path)
                        continue
                    if path.suffix.lower() not in SUFFIX_TO_FILE_TYPE:
                        continue
                    stat_result = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(stat_result.st_mode):
                        continue
                    snapshot.files[str(path)] = _PinnedFile(
                        path=str(path),
                        name=name,
                        parent_fd=directory_fd,
                        size=stat_result.st_size,
                        mtime=stat_result.st_mtime,
                        device=stat_result.st_dev,
                        inode=stat_result.st_ino,
                    )

        visit(root_fd, root)
        return snapshot
    except Exception:
        snapshot.close()
        raise


@contextmanager
def _open_pinned_file(entry: _PinnedFile):
    fd = os.open(
        entry.name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=entry.parent_fd,
    )
    try:
        stat_result = os.fstat(fd)
        if (
            not stat.S_ISREG(stat_result.st_mode)
            or stat_result.st_dev != entry.device
            or stat_result.st_ino != entry.inode
            or stat_result.st_size != entry.size
            or stat_result.st_mtime != entry.mtime
        ):
            raise ExternalRootBindingError("mismatch", "external_file_changed")
        current = dict(_PINNED_READ_PATHS.get() or {})
        current[entry.path] = Path(f"/proc/self/fd/{fd}")
        token = _PINNED_READ_PATHS.set(current)
        try:
            yield
        finally:
            _PINNED_READ_PATHS.reset(token)
    finally:
        os.close(fd)


def _read_path(source_path: Path) -> Path:
    return (_PINNED_READ_PATHS.get() or {}).get(str(source_path), source_path)


def _collection_path_for(
    session: Session, library: ExternalLibrary, source_path: Path
) -> Optional[str]:
    """Resolve the collection (raw '/'-joined) path for a scanned file."""
    library_root = Path(library.root_path).expanduser().resolve(strict=False)
    source_path = source_path.expanduser().resolve(strict=False)
    if library.collection_mode == ExternalLibraryCollectionMode.MIRROR:
        try:
            rel = source_path.parent.relative_to(library_root)
        except ValueError:
            return None
        parts = [p for p in rel.parts if p not in ("", ".")]
        return "/".join(parts) if parts else None
    # SINGLE mode: everything lands in the configured target collection.
    if library.target_collection_id is not None:
        from app.db.models import Collection

        coll = session.get(Collection, library.target_collection_id)
        if coll is not None and coll.deleted_at is None:
            return coll.path
    return None


def _index_external_file(
    session: Session,
    library: ExternalLibrary,
    source_path: Path,
    size: int,
    mtime: float,
) -> None:
    """Index a not-yet-known on-disk file as an external (in-place) artifact."""
    file_type = SUFFIX_TO_FILE_TYPE[source_path.suffix.lower()]
    read_path = _read_path(source_path)
    blob_hash = sha256_file(read_path)
    strategy = _strategy_for(file_type)
    meta, thumb_bytes = _process_external_file(strategy, file_type, read_path)

    model, created = resolve_or_create_model(
        session,
        dedup_hash=blob_hash,
        model_name=source_path.stem,
        actor=None,
    )

    if created or model.collection_id is None:
        coll_path = _collection_path_for(session, library, source_path)
        if coll_path:
            coll = taxonomy.resolve_or_create_collection(session, coll_path)
            if coll is not None:
                model.collection_id = coll.id
                session.add(model)
                session.commit()
                session.refresh(model)

    persist_artifact(
        session,
        model=model,
        staged_path=source_path,
        original_filename=source_path.name,
        file_type=file_type,
        blob_hash=blob_hash,
        meta=meta,
        thumb_bytes=thumb_bytes,
        overwrite_thumbnail=strategy.overwrite_thumbnail,
        move_blob=False,
        dest_key_override=str(source_path),
        is_external=True,
        external_library_id=library.id,
        source_mtime=mtime,
    )


def _reindex_changed(
    session: Session,
    file_row: File,
    source_path: Path,
    size: int,
    mtime: float | None,
    source_entry: SourceEntry | None = None,
) -> bool:
    """Refresh an indexed file whose on-disk size/mtime changed.

    Returns True if the content actually changed (re-parsed + thumbnail rebuilt),
    False when only the mtime moved (we just record the new signature)."""
    read_path = _read_path(source_path)
    new_hash = sha256_file(read_path)
    if source_entry is not None:
        file_row.source_key = source_entry.key
        file_row.source_etag = source_entry.etag
        file_row.source_version_id = source_entry.version_id
    if new_hash == file_row.sha256:
        file_row.size_bytes = size
        file_row.source_mtime = mtime
        file_row.source_verified_at = utcnow()
        session.add(file_row)
        session.commit()
        return False

    file_type = SUFFIX_TO_FILE_TYPE[source_path.suffix.lower()]
    strategy = _strategy_for(file_type)
    meta, thumb_bytes = _process_external_file(strategy, file_type, read_path)

    file_row.sha256 = new_hash
    file_row.size_bytes = size
    file_row.source_mtime = mtime
    file_row.source_verified_at = utcnow()
    file_row.uploaded_at = utcnow()
    session.add(file_row)

    md = session.exec(select(Metadata).where(Metadata.file_id == file_row.id)).first()
    md_fields = {k: v for k, v in meta.items() if k in Metadata.model_fields}
    if md is None:
        session.add(Metadata(file_id=file_row.id, **md_fields))
    else:
        # Facts belong to the previous source hash until the worker replaces
        # them; empty metadata must never expose obsolete dimensions as current.
        for key in Metadata.model_fields:
            if key not in {"id", "file_id", "created_at"}:
                setattr(md, key, None)
        for k, v in md_fields.items():
            setattr(md, k, v)
        session.add(md)
    from app.modules.media.analysis_generations import request_enrichment

    request_enrichment(session, file_row, promote_thumbnail=strategy.overwrite_thumbnail, preserve_metadata=bool(meta))
    content_changed(session, "model", [file_row.model_id])
    session.commit()
    session.refresh(file_row)
    return True


def _remove_external_file(session: Session, file_row: File) -> None:
    """Soft-delete a file whose on-disk source is gone; trash the model if it
    becomes empty. NAS bytes are never touched (the file is already gone)."""
    now = utcnow()
    file_row.deleted_at = now
    session.add(file_row)
    content_changed(session, "model", [file_row.model_id])
    session.commit()

    remaining = session.exec(
        select(File).where(File.model_id == file_row.model_id, live(File))
    ).first()
    if remaining is None:
        model = session.get(Model, file_row.model_id)
        if model is not None and model.deleted_at is None:
            model.deleted_at = now
            model.updated_at = now
            session.add(model)
            content_changed(session, "model", [model.id])
            session.commit()


def _finish(
    session: Session,
    library: ExternalLibrary,
    status: ExternalLibraryScanStatus,
    summary: ScanSummary,
    *,
    claim_token: str,
) -> None:
    session.refresh(library)
    if library.scan_claim_token != claim_token:
        logger.warning(
            "scan[lib=%s] lost claim before terminal update; preserving newer claim",
            library.id,
        )
        return
    library.last_scanned_at = utcnow()
    library.last_scan_status = status
    library.last_scan_summary = json.dumps(summary.as_dict())
    library.scan_claim_token = None
    library.scan_claim_expires_at = None
    library.scan_job_id = None
    library.updated_at = utcnow()
    session.add(library)
    session.commit()


def _remote_uri(library: ExternalLibrary, key: str) -> str:
    return f"source://{library.connection_id}/{key.lstrip('/')}"


def _remote_collection_path(library: ExternalLibrary, key: str) -> str | None:
    if library.collection_mode != ExternalLibraryCollectionMode.MIRROR:
        return None
    prefix = library.source_prefix.strip("/")
    relative = (
        key[len(prefix) :].lstrip("/") if prefix and key.startswith(prefix) else key
    )
    parent = Path(relative).parent.as_posix()
    return None if parent in {"", "."} else parent


def _index_remote_file(
    session: Session,
    library: ExternalLibrary,
    source: LibrarySource,
    entry: SourceEntry,
) -> File:
    source_name = Path(entry.key)
    file_type = SUFFIX_TO_FILE_TYPE[source_name.suffix.lower()]
    with source.materialize(entry.key, expected=entry) as content:
        read_path = content.path
        blob_hash = sha256_file(read_path)
        strategy = _strategy_for(file_type)
        meta, thumb_bytes = _process_external_file(strategy, file_type, read_path)
        model, created = resolve_or_create_model(
            session,
            dedup_hash=blob_hash,
            model_name=source_name.stem,
            actor=None,
        )
        if created or model.collection_id is None:
            collection_path = _remote_collection_path(library, entry.key)
            if collection_path:
                collection = taxonomy.resolve_or_create_collection(
                    session, collection_path
                )
                if collection is not None:
                    model.collection_id = collection.id
                    session.add(model)
                    session.commit()
                    session.refresh(model)
        row = persist_artifact(
            session,
            model=model,
            staged_path=read_path,
            original_filename=source_name.name,
            file_type=file_type,
            blob_hash=blob_hash,
            meta=meta,
            thumb_bytes=thumb_bytes,
            overwrite_thumbnail=strategy.overwrite_thumbnail,
            move_blob=False,
            dest_key_override=_remote_uri(library, entry.key),
            is_external=True,
            external_library_id=library.id,
            source_mtime=source_timestamp(content.entry.modified_at),
            source_entry=content.entry,
        )
        return row


def _reindex_remote_file(
    session: Session,
    file_row: File,
    source: LibrarySource,
    entry: SourceEntry,
) -> bool:
    with source.materialize(entry.key, expected=entry) as content:
        read_path = content.path
        display_path = Path(file_row.path)
        current = dict(_PINNED_READ_PATHS.get() or {})
        current[str(display_path)] = read_path
        token = _PINNED_READ_PATHS.set(current)
        try:
            changed = _reindex_changed(
                session,
                file_row,
                display_path,
                content.entry.size,
                source_timestamp(content.entry.modified_at),
                source_entry=content.entry,
            )
            file_row.source_etag = content.entry.etag
            file_row.source_version_id = content.entry.version_id
            session.add(file_row)
            session.commit()
            return changed
        finally:
            _PINNED_READ_PATHS.reset(token)


def _source_hash_due(file_row: File, now: datetime) -> bool:
    verified_at = file_row.source_verified_at
    return verified_at is None or ensure_utc(verified_at) <= now - _SOURCE_HASH_MAX_AGE


def _remote_markers_unchanged(file_row: File, entry: SourceEntry) -> bool:
    if file_row.size_bytes != entry.size:
        return False
    usable = False
    mtime = source_timestamp(entry.modified_at)
    if mtime is not None:
        usable = True
        if (
            file_row.source_mtime is None
            or abs(file_row.source_mtime - mtime) > _MTIME_TOLERANCE_S
        ):
            return False
    if entry.etag:
        usable = True
        if entry.etag != file_row.source_etag:
            return False
    if entry.version_id and entry.version_id != "null":
        usable = True
        if entry.version_id != file_row.source_version_id:
            return False
    return usable


def _remote_checkpoint(
    session: Session, library: ExternalLibrary
) -> ExternalLibraryCheckpoint:
    checkpoint = session.exec(
        select(ExternalLibraryCheckpoint).where(
            ExternalLibraryCheckpoint.library_id == library.id
        )
    ).first()
    if (
        checkpoint is None
        or checkpoint.complete
        or checkpoint.observed_keys_json != "[]"
        or (checkpoint.cursor is not None and checkpoint.cursor.startswith("["))
    ):
        if checkpoint is not None:
            session.delete(checkpoint)
            session.flush()
        checkpoint = ExternalLibraryCheckpoint(
            library_id=int(library.id), epoch=uuid.uuid4().hex
        )
        session.add(checkpoint)
        session.flush()
    return checkpoint


def scan_remote_library(
    library_id: int,
    *,
    job_id: str | None = None,
    session_factory: SessionFactory | None = None,
) -> dict:
    """Process one bounded remote page; absence is applied only at epoch end."""
    if session_factory is None:
        session_factory = get_session_factory()
    if not _REMOTE_SCAN_LOCK.acquire(blocking=False):
        return {"coalesced": True, "reason": "remote_scan_busy"}
    summary = ScanSummary()
    scan_started = monotonic()
    from app.modules.storage.remote_deadline import remote_budget

    budget = remote_budget(deadline=scan_started + _REMOTE_SLICE_MAX_SECONDS)
    budget.__enter__()
    try:
        with session_factory.scoped_session() as session:
            library = session.get(ExternalLibrary, library_id)
            if library is None:
                raise ValueError(f"external library {library_id} not found")
            if library.source_kind == LibrarySourceKind.MOUNTED:
                raise ValueError("library_source_is_mounted")
            checkpoint = _remote_checkpoint(session, library)
            if (
                checkpoint.backoff_until
                and ensure_utc(checkpoint.backoff_until) > utcnow()
            ):
                return {
                    **summary.as_dict(),
                    "backoff_until": checkpoint.backoff_until.isoformat(),
                }
            source = source_for_library(library)
            library.last_scan_status = ExternalLibraryScanStatus.RUNNING
            session.add(library)
            session.commit()
            try:
                if monotonic() - scan_started >= _REMOTE_SLICE_MAX_SECONDS:
                    raise LibrarySourceError("remote_scan_slice_deadline")
                page = source.list_page(
                    library.source_prefix,
                    cursor=checkpoint.cursor,
                    limit=_REMOTE_PAGE_LIMIT,
                )
                selected: list[SourceEntry] = []
                bytes_in_slice = 0
                consumed = 0
                for index, entry in enumerate(page.entries):
                    supported = Path(entry.key).suffix.lower() in SUFFIX_TO_FILE_TYPE
                    if (
                        supported
                        and bytes_in_slice + entry.size > _REMOTE_SLICE_MAX_BYTES
                    ):
                        if not page.entry_cursors or consumed == 0:
                            raise LibrarySourceError("remote_scan_slice_byte_limit")
                        page = replace(
                            page,
                            next_cursor=page.entry_cursors[consumed - 1],
                            complete=False,
                        )
                        break
                    consumed = index + 1
                    if supported:
                        selected.append(entry)
                        bytes_in_slice += entry.size
                cursors_by_key = dict(
                    zip(
                        (entry.key for entry in page.entries),
                        page.entry_cursors,
                        strict=False,
                    )
                )
                page_keys = [entry.key for entry in selected]
                tombstones = set(
                    session.exec(
                        select(ExternalLibraryTombstone.source_key).where(
                            ExternalLibraryTombstone.library_id == library_id,
                            ExternalLibraryTombstone.cleared_at == None,  # noqa: E711
                            ExternalLibraryTombstone.source_key.in_(page_keys),
                        )
                    ).all()
                )
                live_files = session.exec(
                    select(File).where(
                        File.external_library_id == library_id,
                        live(File),
                        File.source_key.in_(page_keys),
                    )
                ).all()
                by_key = {row.source_key: row for row in live_files if row.source_key}
                from app.modules.sources.remote_discovery import key_hash

                observation_time = utcnow()
                for entry in selected:
                    # Blocking provider and parser calls cannot be interrupted safely.
                    # Enforce the wall-clock slice between objects so a slow source
                    # cannot turn one scheduled scan into an unbounded network job.
                    if monotonic() - scan_started >= _REMOTE_SLICE_MAX_SECONDS:
                        raise LibrarySourceError("remote_scan_slice_deadline")
                    existing = by_key.get(entry.key)
                    if entry.key in tombstones:
                        summary.skipped += 1
                    elif existing is None:
                        existing = _index_remote_file(session, library, source, entry)
                        summary.added += 1
                    elif _remote_markers_unchanged(
                        existing, entry
                    ) and not _source_hash_due(existing, observation_time):
                        summary.skipped += 1
                    elif _reindex_remote_file(session, existing, source, entry):
                        summary.updated += 1
                    else:
                        summary.skipped += 1
                    observation = session.exec(
                        select(ExternalLibraryObservation).where(
                            ExternalLibraryObservation.checkpoint_id == checkpoint.id,
                            ExternalLibraryObservation.key_hash == key_hash(entry.key),
                        )
                    ).first()
                    if observation is None:
                        observation = ExternalLibraryObservation(
                            checkpoint_id=checkpoint.id, key_hash=key_hash(entry.key)
                        )
                    observation.file_id = existing.id if existing is not None else None
                    session.add(observation)
                    if entry.key in cursors_by_key:
                        checkpoint.cursor = cursors_by_key[entry.key]
                        checkpoint.updated_at = utcnow()
                        session.add(checkpoint)
                        session.commit()
                checkpoint.cursor = page.next_cursor
                checkpoint.complete = page.complete
                checkpoint.metadata_ops += page.metadata_ops
                checkpoint.bytes_read += bytes_in_slice
                checkpoint.updated_at = utcnow()
                if page.complete:
                    session.flush()
                    total = session.exec(
                        select(func.count())
                        .select_from(File)
                        .where(
                            File.external_library_id == library_id,
                            live(File),
                            File.source_key != None,  # noqa: E711
                        )
                    ).one()
                    removal_limit = min(25, max(1, total // 100))
                    seen = exists().where(
                        ExternalLibraryObservation.checkpoint_id == checkpoint.id,
                        ExternalLibraryObservation.file_id == File.id,
                    )
                    suppressed = exists().where(
                        ExternalLibraryTombstone.library_id == library_id,
                        ExternalLibraryTombstone.source_key == File.source_key,
                        ExternalLibraryTombstone.cleared_at == None,  # noqa: E711
                    )
                    missing = session.exec(
                        select(File)
                        .where(
                            File.external_library_id == library_id,
                            live(File),
                            File.source_key != None,  # noqa: E711
                            ~seen,
                            ~suppressed,  # noqa: E711
                        )
                        .limit(removal_limit + 1)
                    ).all()
                    observed_count = session.exec(
                        select(func.count())
                        .select_from(ExternalLibraryObservation)
                        .where(
                            ExternalLibraryObservation.checkpoint_id == checkpoint.id,
                        )
                    ).one()
                    if (not observed_count and total) or len(missing) > removal_limit:
                        summary.error = "remote_mass_removal_blocked"
                        summary.aborted = True
                    else:
                        for row in missing:
                            _remove_external_file(session, row)
                            summary.removed += 1
                    checkpoint.completed_at = utcnow()
                session.add(checkpoint)
                library.last_scanned_at = utcnow()
                library.last_scan_status = (
                    ExternalLibraryScanStatus.ERROR
                    if summary.aborted
                    else ExternalLibraryScanStatus.PARTIAL
                    if summary.errors
                    else ExternalLibraryScanStatus.OK
                )
                result = {
                    **summary.as_dict(),
                    "epoch": checkpoint.epoch,
                    "complete": checkpoint.complete,
                    "cursor": checkpoint.cursor,
                    "metadata_ops": checkpoint.metadata_ops,
                    "bytes_read": checkpoint.bytes_read,
                }
                library.last_scan_summary = json.dumps(result)
                library.updated_at = utcnow()
                session.add(library)
                session.commit()
                if checkpoint.complete and page.inventory_id is not None:
                    from sqlalchemy.exc import SQLAlchemyError

                    from app.modules.sources.remote_discovery import retire_inventory

                    try:
                        retire_inventory(page.inventory_id)
                    except SQLAlchemyError:
                        logger.warning("remote discovery inventory cleanup deferred")
                if job_id:
                    registry.update(job_id, state="completed", result=result)
                return result
            except asyncio.CancelledError as exc:
                session.rollback()
                checkpoint = _remote_checkpoint(session, library)
                checkpoint.cursor = (
                    getattr(exc, "discovery_cursor", None) or checkpoint.cursor
                )
                checkpoint.updated_at = utcnow()
                library.last_scan_status = ExternalLibraryScanStatus.PARTIAL
                summary.error = "remote_scan_cancelled"
                summary.aborted = True
                library.last_scan_summary = json.dumps(summary.as_dict())
                session.add(checkpoint)
                session.add(library)
                session.commit()
                raise
            except Exception as exc:
                session.rollback()
                checkpoint = _remote_checkpoint(session, library)
                if getattr(exc, "discovery_cursor", None):
                    checkpoint.cursor = exc.discovery_cursor
                deadline_reached = str(exc) == "remote_scan_slice_deadline"
                checkpoint.backoff_until = (
                    None if deadline_reached else utcnow() + timedelta(hours=24)
                )
                checkpoint.updated_at = utcnow()
                library.last_scanned_at = utcnow()
                library.last_scan_status = (
                    ExternalLibraryScanStatus.PARTIAL
                    if deadline_reached
                    else ExternalLibraryScanStatus.ERROR
                )
                summary.error = str(exc)
                summary.aborted = True
                library.last_scan_summary = json.dumps(summary.as_dict())
                session.add(checkpoint)
                session.add(library)
                session.commit()
                if job_id:
                    registry.update(job_id, state="failed", error=summary.error)
                return summary.as_dict()
    finally:
        budget.__exit__(None, None, None)
        _REMOTE_SCAN_LOCK.release()


def scan_library(
    library_id: int,
    *,
    relative_path: str | None = None,
    job_id: Optional[str] = None,
    session_factory: SessionFactory | None = None,
) -> dict:
    """Reconcile a library's index with its on-disk folder. Returns the summary."""
    if session_factory is None:
        session_factory = get_session_factory()

    with session_factory.scoped_session() as source_session:
        source_library = source_session.get(ExternalLibrary, library_id)
        if (
            source_library is not None
            and source_library.source_kind != LibrarySourceKind.MOUNTED
        ):
            return scan_remote_library(
                library_id, job_id=job_id, session_factory=session_factory
            )

    summary = ScanSummary()
    with session_factory.scoped_session() as session:
        # A request can arrive while a root is unmounted or replaced.  Refuse
        # before claiming/RUNNING so callers never queue work against an
        # untrusted namespace.
        preflight = session.get(ExternalLibrary, library_id)
        if preflight is None:
            raise ValueError(f"external library {library_id} not found")
        try:
            assert_root_binding(preflight)
        except ExternalRootBindingError as exc:
            summary.error = str(exc)
            summary.aborted = True
            preflight.last_scanned_at = utcnow()
            preflight.last_scan_status = ExternalLibraryScanStatus.ERROR
            preflight.last_scan_summary = json.dumps(summary.as_dict())
            preflight.updated_at = utcnow()
            session.add(preflight)
            session.commit()
            if job_id:
                registry.update(job_id, state="failed", error=summary.error)
            return summary.as_dict()
        claim_token = uuid.uuid4().hex
        now = utcnow()
        claimed = session.execute(
            update(ExternalLibrary)
            .execution_options(synchronize_session=False)
            .where(
                ExternalLibrary.id == library_id,
                or_(
                    ExternalLibrary.scan_claim_token == None,  # noqa: E711
                    ExternalLibrary.scan_claim_expires_at <= now,
                ),
            )
            .values(
                scan_claim_token=claim_token,
                scan_claim_expires_at=now + timedelta(hours=1),
                scan_job_id=job_id,
            )
            .returning(ExternalLibrary.id)
        ).scalar_one_or_none()
        session.commit()
        if claimed is None:
            current = session.get(ExternalLibrary, library_id)
            result = {
                "coalesced": True,
                "job_id": current.scan_job_id if current is not None else None,
            }
            if job_id:
                registry.update(job_id, state="completed", result=result)
            return result
        library = session.get(ExternalLibrary, library_id)
        if library is None:
            raise ValueError(f"external library {library_id} not found")

        try:
            assert_root_binding(library)
        except ExternalRootBindingError as exc:
            summary.error = str(exc)
            summary.aborted = True
            _finish(
                session,
                library,
                ExternalLibraryScanStatus.ERROR,
                summary,
                claim_token=claim_token,
            )
            if job_id:
                registry.update(job_id, state="failed", error=summary.error)
            return summary.as_dict()

        library.last_scan_status = ExternalLibraryScanStatus.RUNNING
        session.add(library)
        session.commit()
        pinned_snapshot: _PinnedSnapshot | None = None

        # Everything past the RUNNING commit runs under a blanket guard: only the
        # per-file loop below has its own boundary, so a failure in _walk (a NAS
        # mount dropping mid-scan), the deletion loop, or _finish would otherwise
        # escape with the row stranded RUNNING. libraries_due_for_scan skips
        # RUNNING libraries, so that strands all future scheduled scans until a
        # restart runs reset_orphaned_scans. Instead, always land in a terminal
        # state (#24 follow-up).
        try:
            root = Path(library.root_path).expanduser().resolve(strict=False)

            # Revalidate after the transaction/claim boundary.  A mount may be
            # replaced between the first probe and the actual walk.
            assert_root_binding(library)

            # --- Safety guard: never mass-delete on an unmounted/unreadable root.
            if not root.exists() or not root.is_dir() or not os.access(root, os.R_OK):
                summary.error = "root_path_missing_or_unreadable"
                summary.aborted = True
                _finish(
                    session,
                    library,
                    ExternalLibraryScanStatus.ERROR,
                    summary,
                    claim_token=claim_token,
                )
                logger.warning(
                    "scan[lib=%s] aborted: root %s missing/unreadable",
                    library_id,
                    root,
                )
                if job_id:
                    registry.update(job_id, state="failed", error=summary.error)
                return summary.as_dict()

            # Refresh the detected filesystem class so the UI / watcher know
            # whether real-time watching can work for this root.
            library.fs_kind = detect_fs_kind(root)
            session.add(library)
            session.commit()

            scan_root = root
            if relative_path:
                candidate = (root / relative_path).resolve()
                if candidate != root and root not in candidate.parents:
                    raise ValueError("path_outside_library_root")
                if not candidate.is_dir() or not os.access(candidate, os.R_OK):
                    raise ValueError("path_missing_or_unreadable")
                scan_root = candidate

            # Retain the legacy traversal as a complete-traversal error guard;
            # all catalog truth below comes from the descriptor-pinned snapshot.
            _walk(scan_root)
            pinned_snapshot = _walk_pinned(root, expected_root_marker(library))
            disk = {
                path: (entry.size, entry.mtime)
                for path, entry in pinned_snapshot.files.items()
                if not relative_path
                or path == str(scan_root)
                or path.startswith(str(scan_root) + os.sep)
            }
            # The walk is a pathname snapshot.  Revalidate the marker and root
            # identity before interpreting it as catalog truth; a replacement
            # mount during traversal must not become a new index or deletion
            # set.
            assert_root_binding(library)

            live_files = session.exec(
                select(File).where(
                    File.external_library_id == library_id,
                    live(File),
                )
            ).all()
            if relative_path:
                prefix = str(scan_root) + os.sep
                live_files = [
                    row
                    for row in live_files
                    if row.path == str(scan_root) or row.path.startswith(prefix)
                ]
            db_by_path = {f.path: f for f in live_files}
            tombstones = {
                row.source_key
                for row in session.exec(
                    select(ExternalLibraryTombstone).where(
                        ExternalLibraryTombstone.library_id == library_id,
                        ExternalLibraryTombstone.cleared_at == None,  # noqa: E711
                    )
                ).all()
            }

            if not disk and db_by_path:
                summary.error = "root_empty_aborted"
                summary.aborted = True
                assert pinned_snapshot is not None
                pinned_snapshot.close()
                pinned_snapshot = None
                _finish(
                    session,
                    library,
                    ExternalLibraryScanStatus.ERROR,
                    summary,
                    claim_token=claim_token,
                )
                logger.warning(
                    "scan[lib=%s] aborted: root %s empty but %d indexed files exist",
                    library_id,
                    root,
                    len(db_by_path),
                )
                if job_id:
                    registry.update(job_id, state="failed", error=summary.error)
                return summary.as_dict()

            if job_id:
                registry.update(
                    job_id,
                    state="running",
                    stage="hashing",
                    total_steps=len(disk) or 1,
                    total=len(disk),
                )
            progress_updates = _ScanProgressCoalescer(total=len(disk))
            observation_time = utcnow()

            for index, (path, (size, mtime)) in enumerate(disk.items(), start=1):
                assert_root_binding(library)
                if job_id and progress_updates.should_flush(index):
                    registry.update(
                        job_id,
                        step=index,
                        total_steps=len(disk),
                        label="hashing",
                        stage="hashing",
                        current_item=Path(path).name,
                        processed=index,
                        progress=index / len(disk) * 100,
                    )
                existing = db_by_path.get(path)
                try:
                    source_key = Path(path).relative_to(root).as_posix()
                    if source_key in tombstones and existing is None:
                        summary.skipped += 1
                        continue
                    entry = pinned_snapshot.files.get(path)
                    if entry is None:
                        raise ExternalRootBindingError(
                            "mismatch", "external_snapshot_changed"
                        )
                    with _open_pinned_file(entry):
                        if existing is None:
                            _index_external_file(
                                session, library, Path(path), size, mtime
                            )
                            summary.added += 1
                        elif (
                            existing.size_bytes == size
                            and existing.source_mtime is not None
                            and abs(existing.source_mtime - mtime) <= _MTIME_TOLERANCE_S
                            and not _source_hash_due(existing, observation_time)
                        ):
                            summary.skipped += 1
                        else:
                            if _reindex_changed(
                                session, existing, Path(path), size, mtime
                            ):
                                summary.updated += 1
                            else:
                                summary.skipped += 1
                except Exception as exc:  # noqa: BLE001 — per-file boundary
                    logger.exception("scan[lib=%s] failed on %s", library_id, path)
                    summary.errors.append(f"{path}: {exc}")

            assert_root_binding(library)
            assert pinned_snapshot is not None
            for path, file_row in db_by_path.items():
                assert_root_binding(library)
                if path not in disk:
                    _remove_external_file(session, file_row)
                    summary.removed += 1

            # A clean run is OK; a run that completed but had per-file failures is
            # PARTIAL so the green status never hides a persistent error.
            final_status = (
                ExternalLibraryScanStatus.PARTIAL
                if summary.errors
                else ExternalLibraryScanStatus.OK
            )
            _finish(
                session,
                library,
                final_status,
                summary,
                claim_token=claim_token,
            )
            pinned_snapshot.close()
            pinned_snapshot = None
            logger.info(
                "scan[lib=%s] done added=%d updated=%d removed=%d skipped=%d errors=%d",
                library_id,
                summary.added,
                summary.updated,
                summary.removed,
                summary.skipped,
                len(summary.errors),
            )
            if job_id:
                # The job itself completed even with per-file errors; the PARTIAL
                # signal lives on the library status and in result.errors.
                registry.update(
                    job_id,
                    state="completed",
                    result=summary.as_dict(),
                    processed=len(disk),
                    total=len(disk),
                    succeeded=summary.added + summary.updated,
                    skipped=summary.skipped,
                    failed=len(summary.errors),
                    retryable=bool(summary.errors),
                    failed_items=[
                        {
                            "name": item.split(":", 1)[0],
                            "reason": item.split(":", 1)[-1],
                            "retryable": True,
                        }
                        for item in summary.errors
                    ],
                )
        except Exception as exc:  # noqa: BLE001 — never leave the row RUNNING
            logger.exception("scan[lib=%s] crashed", library_id)
            if pinned_snapshot is not None:
                pinned_snapshot.close()
                pinned_snapshot = None
            summary.error = f"scan_failed: {exc}"
            summary.aborted = True
            # _finish stamps last_scanned_at so the scheduler doesn't immediately
            # re-fire the same failing scan; ERROR is terminal so it's due again.
            _finish(
                session,
                library,
                ExternalLibraryScanStatus.ERROR,
                summary,
                claim_token=claim_token,
            )
            if job_id:
                registry.update(job_id, state="failed", error=summary.error)

    return summary.as_dict()


def purge_library_index(session: Session, library_id: int) -> int:
    """Soft-delete every indexed file for a library and trash now-empty models.

    Used when a library is removed. NAS bytes are never touched. Returns the
    number of files trashed."""
    now = utcnow()
    files = session.exec(
        select(File).where(File.external_library_id == library_id, live(File))
    ).all()
    affected_models: set[int] = set()
    for f in files:
        f.deleted_at = now
        # The library row is about to be deleted and `files.external_library_id` is a
        # RESTRICT foreign key, so a file still pointing at it makes that delete fail.
        # Detaching here rather than relying on the constraint being absent: it is
        # present on a fresh install and missing on an upgraded one, so leaving it set
        # meant the endpoint returned 500 or 200 depending on how the operator's
        # database came to exist.
        f.external_library_id = None
        session.add(f)
        if f.model_id is not None:
            affected_models.add(f.model_id)
    content_changed(session, "model", affected_models)
    session.commit()

    for model_id in affected_models:
        remaining = session.exec(
            select(File).where(File.model_id == model_id, live(File))
        ).first()
        if remaining is None:
            model = session.get(Model, model_id)
            if model is not None and model.deleted_at is None:
                model.deleted_at = now
                model.updated_at = now
                session.add(model)
    content_changed(session, "model", affected_models)
    session.commit()
    return len(files)


def reset_orphaned_scans(session: Session) -> int:
    """Clear scans stranded in RUNNING by a process restart.

    ``scan_library`` marks a library RUNNING for the duration of a scan
    (see :func:`scan_library`). If the process dies mid-scan the row stays
    RUNNING forever, and :func:`libraries_due_for_scan` permanently skips it.
    Call this once at startup: mark any RUNNING library ERROR with an
    interrupted note so the scheduler picks it up again. Returns the count
    reset. Reuses the existing ERROR status — no new enum or migration.

    We also stamp ``last_scanned_at`` so the next attempt waits for the library's
    schedule instead of re-firing on the very next 60s tick. Without this, a scan
    that crashes the process (e.g. a pathological file — issue #24) restarts, is
    immediately due again, and crash-loops the container. The schedule gap turns
    a tight loop into at most one attempt per interval, and a manual scan is
    always still available.
    """
    orphaned = session.exec(
        select(ExternalLibrary).where(
            ExternalLibrary.last_scan_status == ExternalLibraryScanStatus.RUNNING
        )
    ).all()
    now = utcnow()
    for library in orphaned:
        library.last_scan_status = ExternalLibraryScanStatus.ERROR
        library.last_scan_summary = json.dumps({"error": "interrupted by restart"})
        library.last_scanned_at = now
        library.scan_claim_token = None
        library.scan_claim_expires_at = None
        library.scan_job_id = None
        library.updated_at = now
        session.add(library)
    if orphaned:
        session.commit()
    return len(orphaned)


def libraries_due_for_scan(session: Session) -> list[int]:
    """IDs of enabled libraries whose cron schedule has fired (or never ran).

    Manual-only libraries (empty ``scan_schedule``) are never returned here; they
    only scan via ``POST /libraries/{id}/scan``. Libraries already RUNNING are
    skipped to avoid overlapping scans.
    """
    now = utcnow()
    due: list[int] = []
    for lib in session.exec(
        select(ExternalLibrary).where(ExternalLibrary.enabled)
    ).all():
        if lib.id is None:
            continue
        if lib.last_scan_status == ExternalLibraryScanStatus.RUNNING:
            continue
        # last_scanned_at is naive when read back from the DB; ``is_due``
        # normalises it before comparing against the aware ``now``.
        if is_due(lib.scan_schedule, lib.last_scanned_at, now):
            due.append(lib.id)
    return due
