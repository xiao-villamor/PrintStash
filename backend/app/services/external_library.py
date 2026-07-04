"""External library (NAS folder) scan + reconcile engine.

The folder is the source of truth. A scan walks ``root_path`` and reconciles the
index against what is on disk: new files are indexed in place (no copy), removed
files are moved to trash, and changed files are re-hashed and refreshed. Web
uploads/revisions write back into the folder (see ``ingestion.resolve_write_target``)
so the folder stays complete — PrintStash never overwrites or deletes existing bytes.

Safety: a scan never mass-deletes on an unmounted/empty root. If ``root_path`` is
missing/unreadable, or it yields zero candidate files while the library still has
live indexed files, the scan aborts with an error and changes nothing.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from croniter import croniter
from sqlmodel import Session, select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    Document,
    DocumentKind,
    ExternalLibrary,
    ExternalLibraryCollectionMode,
    ExternalLibraryScanStatus,
    ExternalLibraryWatchMode,
    File,
    FileType,
    Metadata,
    Model,
    SUFFIX_TO_FILE_TYPE,
)
from app.db.scopes import live
from app.db.session import SessionFactory, get_session_factory
from app.services import mesh_processing, mesh_retry, taxonomy, thumbnail
from app.services.documents import DOCUMENT_SUFFIX_TO_KIND
from app.services.hashing import sha256_file
from app.services.ingestion import (
    _gcode_strategy,
    _mesh_strategy,
    persist_artifact,
    resolve_or_create_model,
)
from app.services.jobs import registry
from app.services.profile_detection import upsert_detected_profiles
from app.services.storage_backend import get_backend

logger = get_logger(__name__)

# Filesystem mtime granularity varies wildly (FAT rounds to 2 s, SMB/CIFS round,
# floats lose precision on round-trip), so the cheap "unchanged" skip needs real
# slack — 1e-6 absorbed nothing and forced a full sha256 re-hash of every file
# with sub-second mtime jitter on each scan. 2 s covers the worst case (FAT);
# the hash compare in _reindex_changed still catches any genuine edit on the
# next size change, so this only trades a re-hash storm for the cheap skip.
_MTIME_TOLERANCE_S = 2.0

FsKind = Literal["local", "network", "unknown"]

# Filesystem types that do NOT deliver reliable inotify events. Real-time
# watching is disabled for these; they fall back to scheduled scans.
_NETWORK_FSTYPES = {
    "nfs",
    "nfs4",
    "cifs",
    "smbfs",
    "smb3",
    "afs",
    "ncpfs",
    "9p",
}
# Filesystem types that support inotify and are safe to watch.
_LOCAL_FSTYPES = {
    "ext2",
    "ext3",
    "ext4",
    "xfs",
    "btrfs",
    "zfs",
    "f2fs",
    "reiserfs",
    "jfs",
    "overlay",
    "tmpfs",
}


def detect_fs_kind(path: str | os.PathLike[str]) -> FsKind:
    """Classify the filesystem backing *path* as local / network / unknown.

    Used to decide whether real-time watching can work (it can't on network
    mounts). Reads ``/proc/self/mountinfo`` on Linux; anything else (other OS,
    parse failure, fuse, virtiofs, …) returns ``"unknown"`` so the caller treats
    it as "schedule only" unless the user explicitly forces watching.
    """
    target = os.path.realpath(str(path))
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as fh:
            entries = fh.readlines()
    except OSError:
        return "unknown"

    best_mount = ""
    best_fstype = ""
    for line in entries:
        # Format: ... mount_point ... - fstype source super_opts
        parts = line.split(" - ", 1)
        if len(parts) != 2:
            continue
        left = parts[0].split()
        right = parts[1].split()
        if len(left) < 5 or not right:
            continue
        mount_point = left[4]
        fstype = right[0]
        if target == mount_point or target.startswith(mount_point.rstrip("/") + "/"):
            if len(mount_point) >= len(best_mount):
                best_mount = mount_point
                best_fstype = fstype

    if not best_fstype:
        return "unknown"
    base = best_fstype.split(".", 1)[0].lower()  # e.g. "fuse.sshfs" -> "fuse"
    if base in _NETWORK_FSTYPES or "smb" in base or "cifs" in base:
        return "network"
    if base in _LOCAL_FSTYPES:
        return "local"
    return "unknown"


def should_watch(library: ExternalLibrary, fs_kind: FsKind) -> bool:
    """Whether real-time watching is active for *library* given its watch mode."""
    if not library.enabled:
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
    # Phase 2 (mesh_retry.retry_failed_renders, run after the catalog pass
    # below): how many pending/failed meshes got a real thumbnail this time,
    # and how many are still waiting (either genuinely too big, or will be
    # retried again next scan).
    rendered: int = 0
    render_pending: int = 0

    def as_dict(self) -> dict:
        return {
            "added": self.added,
            "updated": self.updated,
            "removed": self.removed,
            "skipped": self.skipped,
            "errors": self.errors,
            "error": self.error,
            "aborted": self.aborted,
            "rendered": self.rendered,
            "render_pending": self.render_pending,
        }


def _strategy_for(file_type: FileType):
    if file_type == FileType.GCODE:
        return _gcode_strategy()
    return _mesh_strategy(file_type)


def _walk(root: Path) -> tuple[dict[str, tuple[int, float]], dict[str, tuple[int, float]]]:
    """Map every supported file under *root* to (size_bytes, mtime).

    Returns ``(models, documents)`` — model files (STL/3MF/G-code/etc, see
    SUFFIX_TO_FILE_TYPE) and document files (PDF/markdown/txt, see
    DOCUMENT_SUFFIX_TO_KIND) found in the same single rglob pass. A folder
    with only a PDF manual and no model is a normal case, not skipped: the
    two dicts are independent, keyed by whichever suffix map matched.
    """
    models: dict[str, tuple[int, float]] = {}
    documents: dict[str, tuple[int, float]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        is_model = suffix in SUFFIX_TO_FILE_TYPE
        is_doc = suffix in DOCUMENT_SUFFIX_TO_KIND
        if not is_model and not is_doc:
            continue
        try:
            st = path.stat()
        except OSError:
            continue
        if is_model:
            models[str(path)] = (st.st_size, st.st_mtime)
        else:
            documents[str(path)] = (st.st_size, st.st_mtime)
    return models, documents


def _collection_path_for(
    session: Session, library: ExternalLibrary, source_path: Path
) -> Optional[str]:
    """Resolve the collection (raw '/'-joined) path for a scanned file."""
    if library.collection_mode == ExternalLibraryCollectionMode.MIRROR:
        try:
            rel = source_path.parent.relative_to(Path(library.root_path))
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
    """Index a not-yet-known on-disk file as an external (in-place) artifact.

    Mesh files (STL/3MF/OBJ/STEP) are catalogued immediately with a "pending"
    placeholder — no load, no render — so a large or risky mesh never blocks
    the rest of the folder from showing up. mesh_retry.retry_failed_renders
    (called once at the end of scan_library) does the actual load+render
    afterward, one file at a time, with more memory headroom than a job
    running mid-scan gets. G-code isn't deferred: parsing it and pulling its
    embedded thumbnail is cheap and carries none of the OOM risk a full mesh
    load does.
    """
    file_type = SUFFIX_TO_FILE_TYPE[source_path.suffix.lower()]
    blob_hash = sha256_file(source_path)
    strategy = _strategy_for(file_type)

    if file_type == FileType.GCODE:
        meta, thumb_bytes = strategy.process(source_path)
    else:
        meta, thumb_bytes = mesh_processing.pending_geometry(), None

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
    upsert_detected_profiles(session, meta)


def _reindex_changed(
    session: Session,
    file_row: File,
    source_path: Path,
    size: int,
    mtime: float,
) -> bool:
    """Refresh an indexed file whose on-disk size/mtime changed.

    Returns True if the content actually changed (re-parsed + thumbnail rebuilt),
    False when only the mtime moved (we just record the new signature)."""
    new_hash = sha256_file(source_path)
    if new_hash == file_row.sha256:
        file_row.size_bytes = size
        file_row.source_mtime = mtime
        session.add(file_row)
        session.commit()
        return False

    file_type = SUFFIX_TO_FILE_TYPE[source_path.suffix.lower()]
    strategy = _strategy_for(file_type)
    if file_type == FileType.GCODE:
        meta, thumb_bytes = strategy.process(source_path)
    else:
        # Same catalog-first deferral as _index_external_file: don't risk an
        # OOM re-rendering a changed mesh inline mid-scan. mesh_retry picks
        # this row up in phase 2 same as a brand-new file would.
        meta, thumb_bytes = mesh_processing.pending_geometry(), None

    file_row.sha256 = new_hash
    file_row.size_bytes = size
    file_row.source_mtime = mtime
    file_row.uploaded_at = utcnow()
    session.add(file_row)
    session.commit()
    session.refresh(file_row)

    backend = get_backend()
    assert file_row.id is not None
    if thumb_bytes:
        backend.write_bytes(thumbnail.to_webp(thumb_bytes), backend.thumbnail_key(file_row.id))
        backend.delete(backend.legacy_thumbnail_key(file_row.id))
    elif file_type != FileType.GCODE:
        # Deferred mesh re-render: the old thumbnail no longer matches the
        # file's new content. Clear it so the UI shows "pending" rather than
        # a stale, now-incorrect render until phase 2 catches up.
        backend.delete(backend.thumbnail_key(file_row.id))

    md = session.exec(select(Metadata).where(Metadata.file_id == file_row.id)).first()
    md_fields = {k: v for k, v in meta.items() if k in Metadata.model_fields}
    if md is None:
        session.add(Metadata(file_id=file_row.id, **md_fields))
    else:
        for k, v in md_fields.items():
            setattr(md, k, v)
        session.add(md)
    session.commit()
    upsert_detected_profiles(session, meta)
    return True


def _remove_external_file(session: Session, file_row: File) -> None:
    """Soft-delete a file whose on-disk source is gone; trash the model if it
    becomes empty. NAS bytes are never touched (the file is already gone)."""
    now = utcnow()
    file_row.deleted_at = now
    session.add(file_row)
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
            session.commit()


def _read_markdown_body(source_path: Path) -> str:
    """Read a scanned markdown/txt file's content, capped the same way the
    manual-upload path caps it (settings.max_upload_bytes) — a stray large
    log file with a .txt extension shouldn't get fully loaded into a DB row.
    """
    try:
        data = source_path.read_bytes()
    except OSError as exc:
        logger.warning("could not read document %s: %s", source_path, exc)
        return ""
    if len(data) > settings.max_upload_bytes:
        data = data[: settings.max_upload_bytes]
    return data.decode("utf-8", errors="replace")


def _index_external_document(
    session: Session,
    library: ExternalLibrary,
    source_path: Path,
    size: int,
    mtime: float,
) -> None:
    """Index a not-yet-known document (PDF/markdown/txt) found in a scanned
    folder — index-in-place, same contract as _index_external_file: bytes are
    never copied, `source_path` stays the source of truth.

    Deliberately not linked to any Model. A document's home is the folder's
    mirrored Collection (see _collection_path_for) — the same Collection a
    model file in that folder would land in — so browsing to a folder in file
    mode and switching to the Documents tab shows it, with no per-model link.
    """
    kind = DOCUMENT_SUFFIX_TO_KIND[source_path.suffix.lower()]
    coll_path = _collection_path_for(session, library, source_path)
    collection_id = None
    if coll_path:
        coll = taxonomy.resolve_or_create_collection(session, coll_path)
        if coll is not None:
            collection_id = coll.id

    doc = Document(
        name=source_path.stem,
        kind=kind,
        collection_id=collection_id,
        is_external=True,
        external_library_id=library.id,
        source_path=str(source_path),
        source_mtime=mtime,
        size_bytes=size,
    )
    if kind == DocumentKind.MARKDOWN:
        doc.body = _read_markdown_body(source_path)
    else:
        doc.filename = source_path.name
        doc.sha256 = sha256_file(source_path)
    session.add(doc)
    session.commit()


def _reindex_external_document_changed(
    session: Session,
    doc: Document,
    source_path: Path,
    size: int,
    mtime: float,
) -> bool:
    """Refresh a scanned document whose on-disk size/mtime changed.

    Returns True if content was actually re-read, False if only the mtime
    ticked (e.g. an SMB re-mount) without the file's size actually changing.
    """
    if (
        doc.source_mtime is not None
        and abs(doc.source_mtime - mtime) <= _MTIME_TOLERANCE_S
        and doc.size_bytes == size
    ):
        return False

    doc.size_bytes = size
    doc.source_mtime = mtime
    doc.updated_at = utcnow()
    if doc.kind == DocumentKind.MARKDOWN:
        doc.body = _read_markdown_body(source_path)
    else:
        doc.sha256 = sha256_file(source_path)
    session.add(doc)
    session.commit()
    return True


def _remove_external_document(session: Session, doc: Document) -> None:
    """Soft-delete a document whose on-disk source is gone. NAS bytes are
    never touched — same contract as _remove_external_file."""
    doc.deleted_at = utcnow()
    session.add(doc)
    session.commit()


def _finish(
    session: Session,
    library: ExternalLibrary,
    status: ExternalLibraryScanStatus,
    summary: ScanSummary,
) -> None:
    library.last_scanned_at = utcnow()
    library.last_scan_status = status
    library.last_scan_summary = json.dumps(summary.as_dict())
    library.updated_at = utcnow()
    session.add(library)
    session.commit()


def scan_library(
    library_id: int,
    *,
    job_id: Optional[str] = None,
    session_factory: SessionFactory | None = None,
) -> dict:
    """Reconcile a library's index with its on-disk folder. Returns the summary."""
    if session_factory is None:
        session_factory = get_session_factory()

    summary = ScanSummary()
    with session_factory.scoped_session() as session:
        library = session.get(ExternalLibrary, library_id)
        if library is None:
            raise ValueError(f"external library {library_id} not found")

        library.last_scan_status = ExternalLibraryScanStatus.RUNNING
        session.add(library)
        session.commit()

        # Everything past the RUNNING commit runs under a blanket guard: only the
        # per-file loop below has its own boundary, so a failure in _walk (a NAS
        # mount dropping mid-scan), the deletion loop, or _finish would otherwise
        # escape with the row stranded RUNNING. libraries_due_for_scan skips
        # RUNNING libraries, so that strands all future scheduled scans until a
        # restart runs reset_orphaned_scans. Instead, always land in a terminal
        # state (#24 follow-up).
        try:
            root = Path(library.root_path)

            # --- Safety guard: never mass-delete on an unmounted/unreadable root.
            if not root.exists() or not root.is_dir() or not os.access(root, os.R_OK):
                summary.error = "root_path_missing_or_unreadable"
                summary.aborted = True
                _finish(session, library, ExternalLibraryScanStatus.ERROR, summary)
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

            disk, disk_docs = _walk(root)

            live_files = session.exec(
                select(File).where(
                    File.external_library_id == library_id,
                    live(File),
                )
            ).all()
            db_by_path = {f.path: f for f in live_files}

            live_docs = session.exec(
                select(Document).where(
                    Document.external_library_id == library_id,
                    Document.is_external == True,  # noqa: E712
                    live(Document),
                )
            ).all()
            docs_by_path = {d.source_path: d for d in live_docs}

            if not disk and not disk_docs and (db_by_path or docs_by_path):
                summary.error = "root_empty_aborted"
                summary.aborted = True
                _finish(session, library, ExternalLibraryScanStatus.ERROR, summary)
                logger.warning(
                    "scan[lib=%s] aborted: root %s empty but %d file(s)/%d document(s) indexed",
                    library_id,
                    root,
                    len(db_by_path),
                    len(docs_by_path),
                )
                if job_id:
                    registry.update(job_id, state="failed", error=summary.error)
                return summary.as_dict()

            total_steps = (len(disk) + len(disk_docs)) or 1
            if job_id:
                registry.update(job_id, state="running", total_steps=total_steps)

            step = 0
            for path, (size, mtime) in disk.items():
                step += 1
                if job_id:
                    registry.update(
                        job_id,
                        step=step,
                        total_steps=total_steps,
                        label=f"scanning {Path(path).name}",
                        progress=step / total_steps * 100,
                    )
                existing = db_by_path.get(path)
                try:
                    if existing is None:
                        _index_external_file(session, library, Path(path), size, mtime)
                        summary.added += 1
                    elif (
                        existing.size_bytes == size
                        and existing.source_mtime is not None
                        and abs(existing.source_mtime - mtime) <= _MTIME_TOLERANCE_S
                    ):
                        summary.skipped += 1
                    else:
                        if _reindex_changed(session, existing, Path(path), size, mtime):
                            summary.updated += 1
                        else:
                            summary.skipped += 1
                except Exception as exc:  # noqa: BLE001 — per-file boundary
                    logger.exception("scan[lib=%s] failed on %s", library_id, path)
                    summary.errors.append(f"{path}: {exc}")

            for path, (size, mtime) in disk_docs.items():
                step += 1
                if job_id:
                    registry.update(
                        job_id,
                        step=step,
                        total_steps=total_steps,
                        label=f"scanning {Path(path).name}",
                        progress=step / total_steps * 100,
                    )
                existing_doc = docs_by_path.get(path)
                try:
                    if existing_doc is None:
                        _index_external_document(session, library, Path(path), size, mtime)
                        summary.added += 1
                    elif (
                        existing_doc.size_bytes == size
                        and existing_doc.source_mtime is not None
                        and abs(existing_doc.source_mtime - mtime) <= _MTIME_TOLERANCE_S
                    ):
                        summary.skipped += 1
                    else:
                        if _reindex_external_document_changed(
                            session, existing_doc, Path(path), size, mtime
                        ):
                            summary.updated += 1
                        else:
                            summary.skipped += 1
                except Exception as exc:  # noqa: BLE001 — per-document boundary
                    logger.exception(
                        "scan[lib=%s] failed on document %s", library_id, path
                    )
                    summary.errors.append(f"{path}: {exc}")

            for path, file_row in db_by_path.items():
                if path not in disk:
                    _remove_external_file(session, file_row)
                    summary.removed += 1

            for path, doc_row in docs_by_path.items():
                if path not in disk_docs:
                    _remove_external_document(session, doc_row)
                    summary.removed += 1

            # A clean run is OK; a run that completed but had per-file failures is
            # PARTIAL so the green status never hides a persistent error.
            final_status = (
                ExternalLibraryScanStatus.PARTIAL
                if summary.errors
                else ExternalLibraryScanStatus.OK
            )
            _finish(session, library, final_status, summary)
            logger.info(
                "scan[lib=%s] catalog phase done added=%d updated=%d removed=%d "
                "skipped=%d errors=%d",
                library_id,
                summary.added,
                summary.updated,
                summary.removed,
                summary.skipped,
                len(summary.errors),
            )
            if job_id and not settings.mesh_retry_after_scan:
                # No render phase to wait for — this is the whole job.
                registry.update(job_id, state="completed", result=summary.as_dict())
            elif job_id:
                registry.update(
                    job_id,
                    state="running",
                    label="rendering_meshes",
                    result=summary.as_dict(),
                )
        except Exception as exc:  # noqa: BLE001 — never leave the row RUNNING
            logger.exception("scan[lib=%s] crashed", library_id)
            summary.error = f"scan_failed: {exc}"
            summary.aborted = True
            # _finish stamps last_scanned_at so the scheduler doesn't immediately
            # re-fire the same failing scan; ERROR is terminal so it's due again.
            _finish(session, library, ExternalLibraryScanStatus.ERROR, summary)
            if job_id:
                registry.update(job_id, state="failed", error=summary.error)

    # Phase 2 — render every pending/failed mesh catalogued above (plus any
    # left over from an earlier scan of this library), one at a time, now
    # that nothing else in this scan is competing for RAM. Runs in its own
    # session, after the phase-1 one above has closed, so a long render pass
    # doesn't hold a transaction open the whole time. Skipped entirely if the
    # scan itself aborted (root missing, crashed, etc.) — nothing to render.
    if settings.mesh_retry_after_scan and not summary.aborted:
        render_result = mesh_retry.retry_failed_renders(
            library_id=library_id,
            session_factory=session_factory,
            job_id=job_id,
            step_offset=total_steps,
        )
        summary.rendered = render_result["recovered"]
        summary.render_pending = len(render_result["still_failing"])
        logger.info(
            "scan[lib=%s] render phase done rendered=%d still_pending=%d",
            library_id,
            summary.rendered,
            summary.render_pending,
        )
        with session_factory.scoped_session() as session:
            library = session.get(ExternalLibrary, library_id)
            if library is not None:
                library.last_scan_summary = json.dumps(summary.as_dict())
                session.add(library)
                session.commit()
        if job_id:
            registry.update(job_id, state="completed", result=summary.as_dict())

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
        session.add(f)
        if f.model_id is not None:
            affected_models.add(f.model_id)
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
    for lib in session.exec(select(ExternalLibrary).where(ExternalLibrary.enabled == True)).all():  # noqa: E712
        if lib.id is None:
            continue
        if lib.last_scan_status == ExternalLibraryScanStatus.RUNNING:
            continue
        # last_scanned_at is naive when read back from the DB; ``is_due``
        # normalises it before comparing against the aware ``now``.
        if is_due(lib.scan_schedule, lib.last_scanned_at, now):
            due.append(lib.id)
    return due
