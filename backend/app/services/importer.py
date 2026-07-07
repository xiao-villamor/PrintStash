"""URL + ZIP import.

Two ingest paths layered on top of the existing ingestion pipeline:

* **URL import** — download a direct file or ``.zip`` from a user-supplied URL
  (SSRF-guarded) into staging, then ingest it.
* **ZIP import** — inspect an uploaded/downloaded archive, let the caller pick
  entries, then extract the selected 3D files and ingest each as its own Model
  grouped under one auto-created Collection.

Security: ``validate_public_url`` blocks SSRF (private/loopback/link-local
ranges, non-HTTP schemes, redirects to private hosts). ``inspect_archive`` /
``extract_selected`` block zip-slip (path traversal) and zip bombs (entry
count + per-entry + total uncompressed size caps).
"""

from __future__ import annotations

import socket
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Optional
from urllib.parse import unquote, urlparse, urlsplit

from app.core.config import settings
from app.core.http_client import get_http_client
from app.core.logging import get_logger
from app.core.url_safety import is_public_ip as _is_public_ip
from app.db.models import SUFFIX_TO_FILE_TYPE
from app.db.session import SessionFactory
from app.services import storage
from app.services.ingestion import ingest_mesh, ingest_orca_gcode
from app.services.jobs import registry

logger = get_logger(__name__)

_GCODE_SUFFIXES = {".gcode", ".g", ".gco", ".bgcode"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
# Importable 3D suffixes are exactly the ones the vault knows how to ingest.
_IMPORTABLE_SUFFIXES = set(SUFFIX_TO_FILE_TYPE.keys())


class ImportError_(Exception):
    """Raised for user-facing import failures (bad URL, unsafe archive, ...)."""


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------


def validate_public_url(url: str) -> None:
    """Reject non-HTTP(S) schemes and hosts that resolve to non-public IPs.

    Raises ``ImportError_`` if the URL is unsafe to fetch server-side.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ImportError_("url_scheme_not_allowed")
    host = parts.hostname
    if not host:
        raise ImportError_("url_host_missing")
    try:
        infos = socket.getaddrinfo(host, parts.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise ImportError_("url_dns_resolution_failed") from exc
    addrs = {info[4][0] for info in infos}
    if not addrs:
        raise ImportError_("url_dns_resolution_failed")
    for addr in addrs:
        if not _is_public_ip(addr):
            raise ImportError_("url_target_not_public")


def _filename_from_url(url: str, fallback: str = "download") -> str:
    name = Path(unquote(urlparse(url).path)).name
    return name or fallback


# ---------------------------------------------------------------------------
# URL download
# ---------------------------------------------------------------------------


async def download_to_staging(url: str) -> tuple[Path, str]:
    """Download ``url`` into the staging dir, re-validating every redirect hop.

    Returns ``(staged_path, original_filename)``. Enforces ``max_upload_bytes``.
    """
    client = get_http_client()
    current = url
    for _ in range(settings.url_import_max_redirects + 1):
        validate_public_url(current)
        async with client.stream(
            "GET", current, follow_redirects=False, timeout=60.0
        ) as resp:
            if resp.is_redirect:
                location = resp.headers.get("location")
                if not location:
                    raise ImportError_("url_redirect_without_location")
                current = str(resp.url.join(location))
                continue
            resp.raise_for_status()
            original_filename = _content_disposition_name(resp) or _filename_from_url(
                current
            )
            suffix = Path(original_filename).suffix.lower() or ".bin"
            staged = settings.incoming_dir / f"{uuid.uuid4().hex}{suffix}"
            staged.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            limit = settings.max_upload_bytes
            with staged.open("wb") as out:
                async for chunk in resp.aiter_bytes(1024 * 1024):
                    written += len(chunk)
                    if written > limit:
                        out.close()
                        staged.unlink(missing_ok=True)
                        raise ImportError_("download_too_large")
                    out.write(chunk)
            return staged, original_filename
    raise ImportError_("url_too_many_redirects")


def _content_disposition_name(resp) -> str | None:
    cd = resp.headers.get("content-disposition", "")
    marker = "filename="
    if marker not in cd:
        return None
    raw = cd.split(marker, 1)[1].lstrip()
    if raw.startswith('"'):
        # Quoted-string: the value runs to the closing quote, so a ';' *inside*
        # the quotes (e.g. filename="a;b.stl") is part of the name, not a param
        # separator.
        raw = raw[1:].split('"', 1)[0]
    else:
        raw = raw.split(";", 1)[0].strip()
    return Path(unquote(raw)).name or None


# ---------------------------------------------------------------------------
# Archive inspection + extraction (zip-slip / zip-bomb safe)
# ---------------------------------------------------------------------------


@dataclass
class ArchiveEntry:
    name: str
    size_bytes: int
    file_type: Optional[str]  # FileType value if importable, else None
    is_image: bool


def _safe_entry_name(name: str) -> bool:
    """Reject absolute paths, drive letters, and any '..' traversal.

    Backslashes are normalised to '/' first so a Windows-style ``..\\..\\evil``
    entry is caught on POSIX too (where ``\\`` is an ordinary filename char and
    would otherwise hide the traversal from ``Path.parts``).
    """
    if not name or name.endswith(("/", "\\")):
        return False
    if name.startswith("/") or name.startswith("\\"):
        return False
    if len(name) > 2 and name[1] == ":":  # windows drive letter
        return False
    p = PurePosixPath(name.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts:
        return False
    return True


def _safe_subdir(rel_name: str) -> str:
    """POSIX directory part of a (validated) entry; ``''`` for a root file."""
    parent = PurePosixPath(rel_name.replace("\\", "/")).parent
    return "" if str(parent) in (".", "") else str(parent)


def inspect_archive(path: Path) -> list[ArchiveEntry]:
    """List archive entries, enforcing zip-bomb caps. Importable + image only."""
    max_entries = settings.max_archive_entries
    max_entry = settings.max_archive_entry_mb * 1024 * 1024
    max_total = settings.max_archive_uncompressed_mb * 1024 * 1024
    out: list[ArchiveEntry] = []
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > max_entries:
                raise ImportError_("archive_too_many_entries")
            total = 0
            for info in infos:
                if info.is_dir() or not _safe_entry_name(info.filename):
                    continue
                if info.file_size > max_entry:
                    raise ImportError_("archive_entry_too_large")
                total += info.file_size
                if total > max_total:
                    raise ImportError_("archive_too_large")
                suffix = Path(info.filename).suffix.lower()
                ft = SUFFIX_TO_FILE_TYPE.get(suffix)
                is_image = suffix in _IMAGE_SUFFIXES
                if ft is None and not is_image:
                    continue
                out.append(
                    ArchiveEntry(
                        name=info.filename,
                        size_bytes=info.file_size,
                        file_type=ft.value if ft else None,
                        is_image=is_image,
                    )
                )
    except zipfile.BadZipFile as exc:
        raise ImportError_("archive_invalid") from exc
    return out


def extract_selected(path: Path, names: list[str]) -> list[tuple[Path, str]]:
    """Extract chosen 3D entries to staging. Returns [(staged_path, rel_name)].

    ``rel_name`` keeps the archive-relative path (e.g. ``Dragons/red.stl``) so
    importers that opt into ``nest_subdirs`` can mirror the folder layout into
    sub-collections; entries at the archive root have no separator and behave
    exactly as a bare filename did before.
    """
    wanted = set(names)
    extracted: list[tuple[Path, str]] = []
    max_entry = settings.max_archive_entry_mb * 1024 * 1024
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.filename not in wanted or info.is_dir():
                continue
            if not _safe_entry_name(info.filename):
                raise ImportError_("archive_unsafe_entry")
            if info.file_size > max_entry:
                raise ImportError_("archive_entry_too_large")
            suffix = Path(info.filename).suffix.lower()
            if suffix not in _IMPORTABLE_SUFFIXES:
                continue
            staged = settings.incoming_dir / f"{uuid.uuid4().hex}{suffix}"
            with zf.open(info) as src:
                storage.stream_to_path(src, staged)
            extracted.append((staged, info.filename.replace("\\", "/")))
    return extracted


# ---------------------------------------------------------------------------
# Pending-archive registry (bridges /ingest/archive -> /select two-step flow)
# ---------------------------------------------------------------------------


@dataclass
class _PendingArchive:
    path: Path
    archive_name: str
    owner_user_id: Optional[int]
    entries: list[ArchiveEntry]
    source_url: Optional[str] = None
    created_at: float = field(default_factory=time.time)


class _ArchiveRegistry:
    """In-process store of staged archives awaiting entry selection (1h TTL)."""

    _TTL = 3600.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, _PendingArchive] = {}

    def add(self, pending: _PendingArchive) -> str:
        archive_id = uuid.uuid4().hex
        with self._lock:
            self._prune()
            self._items[archive_id] = pending
        return archive_id

    def get(self, archive_id: str) -> _PendingArchive | None:
        with self._lock:
            return self._items.get(archive_id)

    def pop(self, archive_id: str) -> _PendingArchive | None:
        with self._lock:
            return self._items.pop(archive_id, None)

    def _prune(self) -> None:
        cutoff = time.time() - self._TTL
        for key in [k for k, v in self._items.items() if v.created_at < cutoff]:
            stale = self._items.pop(key, None)
            if stale is not None:
                stale.path.unlink(missing_ok=True)


archives = _ArchiveRegistry()


# ---------------------------------------------------------------------------
# Grouped import — each 3D file becomes its own Model under one Collection
# ---------------------------------------------------------------------------


def _collection_for_archive(parent: Optional[str], archive_name: str) -> str:
    """Nest an auto collection named after the archive under the chosen parent."""
    base = Path(archive_name).stem or "import"
    if parent and parent.strip():
        return f"{parent.strip().rstrip('/')}/{base}"
    return base


def _ingest_one_file(
    staged: Path,
    original_filename: str,
    *,
    collection: Optional[str],
    tags: Optional[str],
    source_url: Optional[str],
    model_name: Optional[str],
    actor_user_id: Optional[int],
    session_factory: SessionFactory,
) -> Optional[dict]:
    """Ingest one staged file under its own child job.

    Returns a result dict (``model_id``/``file_id``/``name`` on success, or
    ``name``/``error`` on failure), or ``None`` if the suffix is not importable
    (the caller skips it without counting it as a step).

    ``original_filename`` may carry an archive-relative path; only its basename
    is used for the suffix, model name, and stored filename. Callers that want
    the directory mirrored into a sub-collection derive that into ``collection``
    before calling (see ``import_assets``' ``nest_subdirs``).
    """
    original_filename = PurePosixPath(original_filename.replace("\\", "/")).name
    suffix = Path(original_filename).suffix.lower()
    resolved_name = model_name or Path(original_filename).stem
    child = registry.create(owner_user_id=actor_user_id)
    try:
        if suffix in _GCODE_SUFFIXES:
            ingest_orca_gcode(
                job_id=child,
                staged_path=staged,
                original_filename=original_filename,
                model_name=resolved_name,
                collection=collection,
                tags=tags,
                source_hash=None,
                actor_user_id=actor_user_id,
                session_factory=session_factory,
                source_url=source_url,
            )
        else:
            file_type = SUFFIX_TO_FILE_TYPE.get(suffix)
            if file_type is None:
                staged.unlink(missing_ok=True)
                return None
            ingest_mesh(
                job_id=child,
                staged_path=staged,
                original_filename=original_filename,
                model_name=resolved_name,
                collection=collection,
                tags=tags,
                file_type=file_type,
                source_hash=None,
                actor_user_id=actor_user_id,
                session_factory=session_factory,
                source_url=source_url,
            )
        child_status = registry.get(child)
        if child_status and child_status.state == "completed":
            return {
                "model_id": child_status.model_id,
                "file_id": child_status.file_id,
                "name": original_filename,
            }
        err = child_status.error if child_status else "unknown_error"
        return {"name": original_filename, "error": err}
    except Exception as exc:  # noqa: BLE001 — per-file boundary; continue
        logger.exception("import file failed: %s", original_filename)
        staged.unlink(missing_ok=True)
        return {"name": original_filename, "error": str(exc)}


def import_assets(
    *,
    job_id: str,
    staged_files: list[tuple[Path, str]],
    collection: Optional[str],
    tags: Optional[str],
    source_url: Optional[str],
    actor_user_id: Optional[int],
    session_factory: SessionFactory,
    model_name: Optional[str] = None,
    nest_subdirs: bool = False,
) -> None:
    """Ingest each staged 3D file as its own Model, reporting aggregate progress.

    Each file runs through the existing pipeline under its own child job; the
    parent ``job_id`` tracks how many files are done and collects the results.

    ``model_name`` is an optional display-name override; it only applies to a
    single-file import (it makes no sense to name many archive entries alike),
    otherwise each model is named after its filename stem.

    When ``nest_subdirs`` is set, each file's archive-relative directory is
    appended to ``collection`` so a zipped folder tree is mirrored into nested
    sub-collections; otherwise every file lands directly in ``collection``.
    """
    total = len(staged_files)
    if total == 0:
        registry.update(job_id, state="failed", error="no_importable_files")
        return
    override = model_name.strip() if model_name and total == 1 else None
    registry.update(job_id, state="running", total_steps=total)
    results: list[dict] = []
    done = 0
    for staged, rel_name in staged_files:
        file_collection = collection
        if nest_subdirs:
            subdir = _safe_subdir(rel_name)
            if subdir:
                base = (collection or "").rstrip("/")
                file_collection = f"{base}/{subdir}" if base else subdir
        res = _ingest_one_file(
            staged,
            rel_name,
            collection=file_collection,
            tags=tags,
            source_url=source_url,
            model_name=override,
            actor_user_id=actor_user_id,
            session_factory=session_factory,
        )
        if res is None:
            continue
        results.append(res)
        done += 1
        registry.update(job_id, step=done, progress=done / total * 100)

    imported = [r for r in results if r.get("model_id")]
    registry.update(
        job_id,
        state="completed",
        model_id=imported[0]["model_id"] if imported else None,
        result={"imported": len(imported), "total": total, "items": results},
    )


@dataclass
class ResolvedGroup:
    """One resolved source (a collection member) with its staged files.

    ``source_url`` is recorded per group so each member's models point back to
    their own page; ``error`` carries a member that failed to resolve/download.
    """

    source_url: Optional[str]
    title: str
    staged_files: list[tuple[Path, str]] = field(default_factory=list)
    error: Optional[str] = None


def import_resolved_groups(
    *,
    job_id: str,
    groups: list[ResolvedGroup],
    collection: Optional[str],
    tags: Optional[str],
    actor_user_id: Optional[int],
    session_factory: SessionFactory,
) -> None:
    """Ingest many already-staged groups (e.g. collection members) into one
    collection, recording each group's own ``source_url`` on its models."""
    total = sum(len(g.staged_files) for g in groups)
    registry.update(job_id, state="running", total_steps=max(total, 1))
    results: list[dict] = []
    done = 0
    for group in groups:
        if not group.staged_files:
            results.append({"name": group.title, "error": group.error or "no_importable_files"})
            continue
        for staged, original_filename in group.staged_files:
            res = _ingest_one_file(
                staged,
                original_filename,
                collection=collection,
                tags=tags,
                source_url=group.source_url,
                model_name=None,
                actor_user_id=actor_user_id,
                session_factory=session_factory,
            )
            if res is None:
                continue
            results.append({**res, "member": group.title})
            done += 1
            registry.update(job_id, step=done, progress=done / max(total, 1) * 100)

    imported = [r for r in results if r.get("model_id")]
    result = {
        "kind": "collection_import",
        "collection": collection,
        "imported": len(imported),
        "total": total,
        "items": results,
    }

    # Nothing imported means the whole collection failed — every member errored
    # (commonly all ``makerworld_login_required``) or none had importable files.
    # Reporting "completed" here is the bug that made a failed import look OK; so
    # fail the job, and when the members agree on one error code surface it (so
    # the UI shows e.g. the MakerWorld login message rather than a generic one).
    if not imported:
        member_errors = {r["error"] for r in results if r.get("error")}
        error = member_errors.pop() if len(member_errors) == 1 else "collection_import_failed"
        registry.update(job_id, state="failed", error=error, result=result)
        return

    registry.update(
        job_id,
        state="completed",
        model_id=imported[0]["model_id"],
        result=result,
    )
