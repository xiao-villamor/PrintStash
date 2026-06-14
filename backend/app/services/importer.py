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

import ipaddress
import socket
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse, urlsplit

from app.core.config import settings
from app.core.http_client import get_http_client
from app.core.logging import get_logger
from app.db.models import SUFFIX_TO_FILE_TYPE
from app.db.session import SessionFactory
from app.services import storage
from app.services.ingestion import ingest_mesh, ingest_orca_gcode
from app.services.jobs import registry

logger = get_logger(__name__)

_GCODE_SUFFIXES = {".gcode", ".g", ".gco"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
# Importable 3D suffixes are exactly the ones the vault knows how to ingest.
_IMPORTABLE_SUFFIXES = set(SUFFIX_TO_FILE_TYPE.keys())


class ImportError_(Exception):
    """Raised for user-facing import failures (bad URL, unsafe archive, ...)."""


# ---------------------------------------------------------------------------
# SSRF guard
# ---------------------------------------------------------------------------


def _is_public_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


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
    if marker in cd:
        raw = cd.split(marker, 1)[1].strip().strip('"').split(";")[0]
        return Path(unquote(raw)).name or None
    return None


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
    """Reject absolute paths, drive letters, and any '..' traversal."""
    if not name or name.endswith("/"):
        return False
    if name.startswith("/") or name.startswith("\\"):
        return False
    p = Path(name)
    if p.is_absolute() or ".." in p.parts:
        return False
    if len(name) > 2 and name[1] == ":":  # windows drive letter
        return False
    return True


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
    """Extract chosen 3D entries to staging. Returns [(staged_path, filename)]."""
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
            extracted.append((staged, Path(info.filename).name))
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


def import_assets(
    *,
    job_id: str,
    staged_files: list[tuple[Path, str]],
    collection: Optional[str],
    tags: Optional[str],
    source_url: Optional[str],
    actor_user_id: Optional[int],
    session_factory: SessionFactory,
) -> None:
    """Ingest each staged 3D file as its own Model, reporting aggregate progress.

    Each file runs through the existing pipeline under its own child job; the
    parent ``job_id`` tracks how many files are done and collects the results.
    """
    total = len(staged_files)
    if total == 0:
        registry.update(job_id, state="failed", error="no_importable_files")
        return
    registry.update(job_id, state="running", total_steps=total)
    results: list[dict] = []
    done = 0
    for staged, original_filename in staged_files:
        suffix = Path(original_filename).suffix.lower()
        child = registry.create(owner_user_id=actor_user_id)
        try:
            if suffix in _GCODE_SUFFIXES:
                ingest_orca_gcode(
                    job_id=child,
                    staged_path=staged,
                    original_filename=original_filename,
                    model_name=Path(original_filename).stem,
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
                    continue
                ingest_mesh(
                    job_id=child,
                    staged_path=staged,
                    original_filename=original_filename,
                    model_name=Path(original_filename).stem,
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
                results.append(
                    {
                        "model_id": child_status.model_id,
                        "file_id": child_status.file_id,
                        "name": original_filename,
                    }
                )
            else:
                err = child_status.error if child_status else "unknown_error"
                results.append({"name": original_filename, "error": err})
        except Exception as exc:  # noqa: BLE001 — per-file boundary; continue
            logger.exception("import_assets file failed: %s", original_filename)
            staged.unlink(missing_ok=True)
            results.append({"name": original_filename, "error": str(exc)})
        done += 1
        registry.update(job_id, step=done, progress=done / total * 100)

    imported = [r for r in results if r.get("model_id")]
    registry.update(
        job_id,
        state="completed",
        model_id=imported[0]["model_id"] if imported else None,
        result={"imported": len(imported), "total": total, "items": results},
    )
