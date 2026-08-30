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

import contextvars
import errno
import json
import os
import secrets
import stat
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from time import monotonic
from typing import Optional

from croniter import croniter
from sqlalchemy import or_, update
from sqlmodel import Session, select

from app.core.logging import get_logger
from app.core.time import ensure_utc, utcnow
from app.db.models import (
    SUFFIX_TO_FILE_TYPE,
    ExternalLibrary,
    ExternalLibraryCollectionMode,
    ExternalLibraryScanStatus,
    ExternalLibraryWatchMode,
    File,
    FileType,
    Metadata,
    Model,
)
from app.db.scopes import live
from app.db.session import SessionFactory, get_session_factory
from app.services import taxonomy, thumbnail
from app.services.filesystem import FsKind, detect_fs_kind
from app.services.hashing import sha256_file
from app.services.ingestion import (
    _gcode_strategy,
    _mesh_strategy,
    persist_artifact,
    resolve_or_create_model,
)
from app.services.jobs import registry
from app.services.profile_detection import upsert_detected_profiles
from app.services.storage_backend import StorageCollisionError, get_backend
from app.services.storage_ownership import publish_bytes

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

ROOT_MARKER_FILENAME = ".printstash-external-root.json"
ROOT_MARKER_FORMAT = 1
ROOT_MARKER_ROLE = "external-library"
_ROOT_MARKER_KEYS = {
    "format",
    "installation",
    "role",
    "library_id",
    "root_identity",
}
_ROOT_MARKER_MAX_BYTES = 4096
_PINNED_READ_PATHS: contextvars.ContextVar[dict[str, Path] | None] = (
    contextvars.ContextVar("external_library_pinned_read_paths", default=None)
)


class ExternalRootBindingError(RuntimeError):
    """The configured external root is not the directory previously enrolled."""

    def __init__(self, state: str, message: str | None = None) -> None:
        self.state = state
        super().__init__(message or f"external_root_{state}")


def _installation_identity() -> str:
    from app.core.config import _overlay, settings

    identity = str(_overlay.get("storage_identity") or settings.storage_identity or "")
    if len(identity) != 64 or any(
        char not in "0123456789abcdefABCDEF" for char in identity
    ):
        return ""
    return identity.lower()


def _marker_payload(library: ExternalLibrary, identity: str) -> dict[str, object]:
    return {
        "format": ROOT_MARKER_FORMAT,
        "installation": identity,
        "role": ROOT_MARKER_ROLE,
        "library_id": library.id,
        "root_identity": library.root_identity,
    }


def expected_root_marker(library: ExternalLibrary) -> dict[str, object]:
    """Return the exact marker payload required for this enrolled library."""
    return _marker_payload(library, _installation_identity())


def _validate_marker_payload(actual: object) -> dict[str, object]:
    if not isinstance(actual, dict) or set(actual) != _ROOT_MARKER_KEYS:
        raise ValueError("root_marker_invalid")
    if type(actual["format"]) is not int or actual["format"] != ROOT_MARKER_FORMAT:
        raise ValueError("root_marker_invalid")
    installation = actual["installation"]
    token = actual["root_identity"]
    if (
        not isinstance(installation, str)
        or len(installation) != 64
        or any(char not in "0123456789abcdefABCDEF" for char in installation)
        or actual["role"] != ROOT_MARKER_ROLE
        or type(actual["library_id"]) is not int
        or actual["library_id"] <= 0
        or not isinstance(token, str)
        or len(token) != 64
        or any(char not in "0123456789abcdefABCDEF" for char in token)
    ):
        raise ValueError("root_marker_invalid")
    return actual


def read_root_marker_fd(root_fd: int) -> dict[str, object]:
    """Parse a marker through a pinned root descriptor, fail-closed."""
    marker_fd = os.open(
        ROOT_MARKER_FILENAME,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        dir_fd=root_fd,
    )
    try:
        marker_stat = os.fstat(marker_fd)
        if (
            not stat.S_ISREG(marker_stat.st_mode)
            or marker_stat.st_size > _ROOT_MARKER_MAX_BYTES
        ):
            raise ValueError("root_marker_invalid")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(marker_fd, 1024):
            total += len(chunk)
            if total > _ROOT_MARKER_MAX_BYTES:
                raise ValueError("root_marker_invalid")
            chunks.append(chunk)
        return _validate_marker_payload(json.loads(b"".join(chunks).decode("utf-8")))
    finally:
        os.close(marker_fd)


def _read_root_marker(root: Path) -> dict[str, object]:
    root_fd = os.open(
        root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        return read_root_marker_fd(root_fd)
    finally:
        os.close(root_fd)


def _read_root_state(library: ExternalLibrary) -> tuple[str, str | None]:
    """Return the durable binding state without changing the filesystem."""
    if not library.root_identity:
        # A legacy row is unbound, but an orphan/conflicting marker must still
        # be surfaced instead of being presented as safely enrollable.
        root = Path(library.root_path).expanduser().resolve(strict=False)
        if root.is_dir():
            try:
                actual = _read_root_marker(root)
            except FileNotFoundError:
                actual = None
            except PermissionError:
                return "unreadable", "root_marker_unreadable"
            except OSError as exc:
                if getattr(exc, "errno", None) == errno.ELOOP:
                    return "invalid", "root_marker_invalid"
                return "unreadable", "root_marker_unreadable"
            except (ValueError, TypeError):
                return "invalid", "root_marker_invalid"
            if actual is not None:
                if (
                    isinstance(actual, dict)
                    and actual.get("format") == ROOT_MARKER_FORMAT
                    and actual.get("installation") == _installation_identity()
                    and actual.get("role") == ROOT_MARKER_ROLE
                    and actual.get("library_id") == library.id
                    and isinstance(actual.get("root_identity"), str)
                    and len(actual["root_identity"]) == 64
                    and all(
                        char in "0123456789abcdefABCDEF"
                        for char in actual["root_identity"]
                    )
                ):
                    return "unbound", "orphan_marker_requires_reenrollment"
                return "mismatch", "root_marker_conflict"
        return "unbound", "legacy_library_requires_explicit_enrollment"
    if len(library.root_identity) != 64 or any(
        char not in "0123456789abcdefABCDEF" for char in library.root_identity
    ):
        return "invalid", "invalid_root_identity"
    root = Path(library.root_path).expanduser().resolve(strict=False)
    try:
        if not root.exists() or not root.is_dir():
            return "missing", "root_path_missing"
        if not os.access(root, os.R_OK):
            return "unreadable", "root_path_unreadable"
        actual = _read_root_marker(root)
    except FileNotFoundError:
        return "missing", "root_marker_missing"
    except PermissionError:
        return "unreadable", "root_marker_unreadable"
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ELOOP:
            return "invalid", "root_marker_invalid"
        return "unreadable", "root_marker_unreadable"
    except UnicodeError:
        return "invalid", "root_marker_invalid"
    except (ValueError, TypeError):
        return "invalid", "root_marker_invalid"
    if not isinstance(actual, dict):
        return "invalid", "root_marker_invalid"
    expected = _marker_payload(library, _installation_identity())
    if actual != expected:
        return "mismatch", "root_marker_mismatch"
    return "bound", None


def root_binding_state(library: ExternalLibrary) -> tuple[str, str | None]:
    """Expose the read-only root binding probe for API and watcher callers."""
    return _read_root_state(library)


def assert_root_binding(library: ExternalLibrary) -> None:
    """Fail closed before any scan, indexing, or external write operation."""
    state, reason = _read_root_state(library)
    if state != "bound":
        raise ExternalRootBindingError(state, reason)


def _create_marker(root: Path, payload: dict[str, object]) -> bool:
    """Create a marker without replacing a file another owner supplied."""
    data = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    root_fd = os.open(
        root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    temporary = f".{ROOT_MARKER_FILENAME}.{uuid.uuid4().hex}.tmp"
    try:
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=root_fd
        )
        try:
            written = 0
            while written < len(data):
                count = os.write(fd, data[written:])
                if count <= 0:
                    raise OSError("external root marker short write")
                written += count
            os.fsync(fd)
        finally:
            os.close(fd)
        # link() is create-only, unlike replace(), so a concurrent valid marker
        # can never be silently overwritten.
        os.link(temporary, ROOT_MARKER_FILENAME, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        os.fsync(root_fd)
        return True
    except FileExistsError:
        return False
    finally:
        try:
            os.unlink(temporary, dir_fd=root_fd)
        except FileNotFoundError:
            pass
        os.close(root_fd)


def _refsync_marker(root: Path, expected: dict[str, object]) -> None:
    """Re-read and fsync an orphan marker before adopting its identity."""
    root_fd = os.open(
        root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        actual = read_root_marker_fd(root_fd)
        if actual != expected:
            raise ValueError("root_marker_changed")
        marker_fd = os.open(
            ROOT_MARKER_FILENAME,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
        try:
            os.fsync(marker_fd)
        finally:
            os.close(marker_fd)
        os.fsync(root_fd)
    finally:
        os.close(root_fd)


def enroll_external_root(session: Session, library: ExternalLibrary) -> ExternalLibrary:
    """Explicitly bind an existing root and commit the DB identity atomically.

    The caller must already have authenticated the operation.  The root is
    never created.  Existing markers are accepted only when they exactly match
    this library and installation; every other marker is a conflict.
    """
    root = Path(library.root_path).expanduser().resolve(strict=False)
    if not root.exists() or not root.is_dir():
        raise ExternalRootBindingError("missing", "root_path_missing")
    if not os.access(root, os.R_OK | os.W_OK):
        raise ExternalRootBindingError("unreadable", "root_path_unreadable")
    identity = _installation_identity()
    if not identity:
        raise ExternalRootBindingError("invalid", "installation_identity_missing")
    if library.id is None:
        # Persist the row/ID first.  If marker creation or its directory fsync
        # fails, the operator still has a visible unbound row to retry; a
        # trusted marker can never outlive a nonexistent database row.
        session.add(library)
        session.flush()
        session.commit()
    existing_identity = library.root_identity
    if existing_identity:
        state, reason = _read_root_state(library)
        if state == "bound":
            return library
        # A root can be replaced while retaining its configured path.  An
        # explicit administrator re-enrollment may adopt that markerless
        # replacement, but it must rotate the token so the old mount cannot
        # become trusted again.  Any marker (even malformed) is a conflict.
        if not (state == "missing" and reason == "root_marker_missing"):
            raise ExternalRootBindingError(state, reason)
    library.root_identity = secrets.token_hex(32)
    payload = _marker_payload(library, identity)
    created = False
    try:
        try:
            actual = _read_root_marker(root)
        except FileNotFoundError:
            actual = None
        except PermissionError as exc:
            raise ExternalRootBindingError(
                "unreadable", "root_marker_unreadable"
            ) from exc
        except OSError as exc:
            raise ExternalRootBindingError("invalid", "root_marker_invalid") from exc
        except (UnicodeError, ValueError, TypeError) as exc:
            raise ExternalRootBindingError("invalid", "root_marker_invalid") from exc
        if actual is not None:
            # A previous enrollment whose DB commit had an unknown outcome may
            # leave our own valid marker behind. Explicit admin enrollment may
            # recover that exact marker, but never adopt another library's one.
            if (
                actual["installation"].lower() != identity
                or actual["library_id"] != library.id
            ):
                raise ExternalRootBindingError("mismatch", "root_marker_conflict")
            _refsync_marker(root, actual)
            library.root_identity = str(actual["root_identity"])
            payload = actual
        else:
            created = _create_marker(root, payload)
            if not created:
                raise ExternalRootBindingError("mismatch", "root_marker_conflict")
        session.add(library)
        session.commit()
        session.refresh(library)
        return library
    except Exception:
        session.rollback()
        if created:
            # A commit exception has an unknown outcome.  Preserve the marker
            # rather than unlinking by pathname: a concurrent remount or
            # replacement could make that path refer to somebody else's bytes.
            # A marker without a durable matching DB row is never trusted and
            # requires an operator-visible conflict resolution.
            logger.warning(
                "external root marker preserved after enrollment commit failure",
                extra={"library_id": library.id, "root": str(root)},
            )
        library.root_identity = existing_identity
        raise


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
    if file_type == FileType.GCODE:
        return _gcode_strategy()
    return _mesh_strategy(file_type)


def _process_external_file(
    strategy, file_type: FileType, read_path: Path
) -> tuple[dict, bytes | None]:
    """Process a descriptor-pinned file with its canonical catalog type.

    ``/proc/self/fd/N`` is intentionally suffixless.  Mesh processing therefore
    receives the immutable catalog type explicitly, while all reads remain
    anchored to the already-open descriptor and never reopen the configured
    external root by path.
    """
    if file_type == FileType.GCODE:
        return strategy.process(read_path)
    from app.services import mesh_processing

    return mesh_processing.analyze_mesh(read_path, file_type=file_type.value)


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
    read_path = _read_path(source_path)
    new_hash = sha256_file(read_path)
    if new_hash == file_row.sha256:
        file_row.size_bytes = size
        file_row.source_mtime = mtime
        session.add(file_row)
        session.commit()
        return False

    file_type = SUFFIX_TO_FILE_TYPE[source_path.suffix.lower()]
    strategy = _strategy_for(file_type)
    meta, thumb_bytes = _process_external_file(strategy, file_type, read_path)

    file_row.sha256 = new_hash
    file_row.size_bytes = size
    file_row.source_mtime = mtime
    file_row.uploaded_at = utcnow()
    session.add(file_row)

    md = session.exec(select(Metadata).where(Metadata.file_id == file_row.id)).first()
    md_fields = {k: v for k, v in meta.items() if k in Metadata.model_fields}
    if md is None:
        session.add(Metadata(file_id=file_row.id, **md_fields))
    else:
        for k, v in md_fields.items():
            setattr(md, k, v)
        session.add(md)
    # Signature and parsed metadata are one logical observation of the NAS
    # source. A failed commit leaves both old so the next scan retries parsing.
    session.commit()
    session.refresh(file_row)

    backend = get_backend()
    assert file_row.id is not None
    if thumb_bytes:
        try:
            publish_bytes(
                session,
                backend,
                backend.thumbnail_key(file_row.id),
                thumbnail.to_webp(thumb_bytes),
                object_kind="thumbnail",
            )
            session.commit()
        except (StorageCollisionError, ValueError):
            # Existing thumbnails are never replaced without a separate,
            # receipt-validated replacement primitive. Metadata reindexing can
            # still succeed; preserving a stale derived image is safe.
            logger.warning(
                "external reindex preserved existing thumbnail for file %s",
                file_row.id,
            )

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
