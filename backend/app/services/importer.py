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

import os
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Optional
from urllib.parse import unquote, urlparse

import httpx
from printstash_core.files import (
    ArchiveEntry,
    ArchiveLimits,
    ArchivePolicyError,
    safe_entry_name,
)
from printstash_core.files import (
    extract_selected as extract_selected_archive_entries,
)
from printstash_core.files import (
    inspect_archive as inspect_archive_entries,
)
from printstash_core.files import (
    safe_subdir as _safe_subdir,
)
from printstash_core.imports import StagedAsset

from app.core.config import settings
from app.core.logging import get_logger
from app.core.url_safety import (
    PinnedTarget,
    UnsafeUrlError,
    pinned_transport,
    resolve_public_target,
)
from app.db.models import SUFFIX_TO_FILE_TYPE
from app.db.session import SessionFactory
from app.services.ingestion import ingest_mesh, ingest_orca_gcode
from app.services.jobs import registry

if TYPE_CHECKING:
    from app.services.provenance import ProvenanceContext

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


def _resolve_or_raise(url: str) -> PinnedTarget:
    """Resolve *url* once, or raise the importer's error type."""
    try:
        return resolve_public_target(url)
    except UnsafeUrlError as exc:
        raise ImportError_(exc.reason) from exc


def validate_public_url(url: str) -> None:
    """Reject non-HTTP(S) schemes and hosts that resolve to non-public IPs.

    Raises ``ImportError_`` if the URL is unsafe to fetch server-side. Callers
    that go on to *fetch* the URL must use the address this resolution returned
    (see ``download_to_staging``); re-resolving reopens a DNS-rebind window.
    """
    _resolve_or_raise(url)


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
    current = url
    for _ in range(settings.url_import_max_redirects + 1):
        # Resolve once and dial exactly that address: validating the hostname and
        # then letting httpx resolve it again would let a hostile DNS server
        # answer 127.0.0.1 the second time. Each redirect hop is a fresh URL, so
        # each gets its own validation and its own pinned connection.
        target = _resolve_or_raise(current)
        async with httpx.AsyncClient(
            transport=pinned_transport(target), timeout=60.0
        ) as client:
            async with client.stream("GET", current, follow_redirects=False) as resp:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise ImportError_("url_redirect_without_location")
                    current = str(resp.url.join(location))
                    continue
                resp.raise_for_status()
                original_filename = _content_disposition_name(
                    resp
                ) or _filename_from_url(current)
                suffix = Path(original_filename).suffix.lower() or ".bin"
                staged = settings.incoming_dir / f"{uuid.uuid4().hex}{suffix}"
                staged.parent.mkdir(parents=True, exist_ok=True)
                fd, temp_name = tempfile.mkstemp(
                    prefix=".printstash-url-", dir=staged.parent
                )
                temp = Path(temp_name)
                written = 0
                limit = settings.max_upload_bytes
                try:
                    with os.fdopen(fd, "wb") as out:
                        async for chunk in resp.aiter_bytes(1024 * 1024):
                            written += len(chunk)
                            if written > limit:
                                raise ImportError_("download_too_large")
                            out.write(chunk)
                        out.flush()
                        os.fsync(out.fileno())
                    os.link(temp, staged, follow_symlinks=False)
                    return staged, original_filename
                finally:
                    try:
                        temp.unlink(missing_ok=True)
                    except OSError:
                        pass
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


def _safe_entry_name(name: str) -> bool:
    """Compatibility wrapper for callers testing archive path policy."""
    return safe_entry_name(name)


def inspect_archive(path: Path) -> list[ArchiveEntry]:
    """List archive entries, enforcing zip-bomb caps. Importable + image only."""
    try:
        return inspect_archive_entries(
            path,
            limits=ArchiveLimits(
                max_entries=settings.max_archive_entries,
                max_entry_bytes=settings.max_archive_entry_mb * 1024 * 1024,
                max_total_bytes=settings.max_archive_uncompressed_mb * 1024 * 1024,
                max_central_directory_bytes=(
                    settings.max_archive_central_directory_mb * 1024 * 1024
                ),
                max_path_bytes=settings.max_archive_path_bytes,
                max_depth=settings.max_archive_depth,
            ),
            file_types={
                suffix: file_type.value
                for suffix, file_type in SUFFIX_TO_FILE_TYPE.items()
            },
            image_suffixes=_IMAGE_SUFFIXES,
        )
    except ArchivePolicyError as exc:
        raise ImportError_(exc.code) from exc


def extract_selected(path: Path, names: list[str]) -> list[tuple[Path, str]]:
    """Extract chosen 3D entries to staging. Returns [(staged_path, rel_name)].

    ``rel_name`` keeps the archive-relative path (e.g. ``Dragons/red.stl``) so
    importers that opt into ``nest_subdirs`` can mirror the folder layout into
    sub-collections; entries at the archive root have no separator and behave
    exactly as a bare filename did before.
    """
    max_entry = settings.max_archive_entry_mb * 1024 * 1024
    try:
        return extract_selected_archive_entries(
            path,
            names,
            staging_dir=settings.incoming_dir,
            max_entry_bytes=max_entry,
            importable_suffixes=_IMPORTABLE_SUFFIXES,
        )
    except ArchivePolicyError as exc:
        raise ImportError_(exc.code) from exc


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
    claimed: bool = False


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

    def claim(self, archive_id: str) -> _PendingArchive | None:
        with self._lock:
            item = self._items.get(archive_id)
            if item is None or item.claimed:
                return None
            item.claimed = True
            return item

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
    provenance_context: ProvenanceContext | None = None,
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
    child = registry.create(owner_user_id=actor_user_id, visible=False, kind="artifact")
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
                provenance_context=provenance_context,
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
                provenance_context=provenance_context,
            )
        child_status = registry.get(child)
        if child_status and child_status.state == "completed":
            return {
                "model_id": child_status.model_id,
                "file_id": child_status.file_id,
                "name": original_filename,
                "deduplicated": child_status.deduplicated > 0,
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
    staged_files: list[tuple[Path, str] | StagedAsset],
    collection: Optional[str],
    tags: Optional[str],
    source_url: Optional[str],
    actor_user_id: Optional[int],
    session_factory: SessionFactory,
    model_name: Optional[str] = None,
    nest_subdirs: bool = False,
    inbox_item_id: int | None = None,
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
    registry.update(
        job_id, state="running", total_steps=total, total=total, stage="ingesting"
    )
    results: list[dict] = []
    done = 0
    for staged_file in staged_files:
        if isinstance(staged_file, StagedAsset):
            staged, rel_name = (
                staged_file.staged_path,
                staged_file.resolved.source_filename,
            )
            file_source_url = staged_file.resolved.member_url or source_url
            provenance_context = _provenance_context(
                staged=staged_file,
                inbox_item_id=inbox_item_id,
                actor_user_id=actor_user_id,
            )
        else:
            staged, rel_name = staged_file
            file_source_url = source_url
            provenance_context = None
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
            source_url=file_source_url,
            model_name=override,
            actor_user_id=actor_user_id,
            session_factory=session_factory,
            provenance_context=provenance_context,
        )
        if res is None:
            continue
        if isinstance(staged_file, StagedAsset):
            res = {
                **res,
                "source_selection_id": staged_file.source_selection_id,
                "result_key": staged_file.result_key,
            }
        results.append(res)
        done += 1
        registry.update(job_id, step=done, progress=done / total * 100)

    imported = [r for r in results if r.get("model_id")]
    failures = [r for r in results if r.get("error")]
    deduplicated = sum(bool(r.get("deduplicated")) for r in imported)
    registry.update(
        job_id,
        state="completed" if imported else "failed",
        model_id=imported[0]["model_id"] if imported else None,
        result={"imported": len(imported), "total": total, "items": results},
        processed=len(results),
        total=total,
        succeeded=len(imported),
        deduplicated=deduplicated,
        skipped=max(0, total - len(results)),
        failed=len(failures),
        error="import_failed" if not imported else None,
        retryable=bool(failures),
        failed_items=[
            {
                "name": r.get("name", "item"),
                "reason": r.get("error", "import_failed"),
                "retryable": True,
            }
            for r in failures
        ],
    )


def _provenance_context(
    *,
    staged: StagedAsset,
    inbox_item_id: int | None,
    actor_user_id: int | None,
) -> ProvenanceContext | None:
    """Create provenance context only for the typed V2 asset path.

    Legacy tuple callers remain fully compatible and deliberately cannot
    accidentally fabricate capture provenance.
    """
    from app.services.provenance import ProvenanceContext

    return ProvenanceContext(
        manifest=staged.manifest,
        source_file_id=staged.resolved.source_file_id,
        source_filename=staged.resolved.source_filename,
        source_selection_id=staged.source_selection_id,
        container_entry_path=staged.container_entry_path,
        blob_sha256=staged.blob_sha256,
        inbox_item_id=inbox_item_id,
        actor_id=actor_user_id,
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
    registry.update(
        job_id,
        state="running",
        total_steps=max(total, 1),
        total=total,
        stage="ingesting",
    )
    results: list[dict] = []
    done = 0
    for group in groups:
        if not group.staged_files:
            results.append(
                {"name": group.title, "error": group.error or "no_importable_files"}
            )
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
    failures = [r for r in results if r.get("error")]
    deduplicated = sum(bool(r.get("deduplicated")) for r in imported)
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
        error = (
            member_errors.pop()
            if len(member_errors) == 1
            else "collection_import_failed"
        )
        registry.update(
            job_id,
            state="failed",
            error=error,
            result=result,
            processed=len(results),
            total=total,
            failed=len(failures),
            retryable=True,
            failed_items=[
                {
                    "name": r.get("name", "item"),
                    "reason": r.get("error", error),
                    "retryable": True,
                }
                for r in failures
            ],
        )
        return

    registry.update(
        job_id,
        state="completed",
        model_id=imported[0]["model_id"],
        result=result,
        processed=len(results),
        total=total,
        succeeded=len(imported),
        deduplicated=deduplicated,
        skipped=max(0, total - len(results)),
        failed=len(failures),
        retryable=bool(failures),
        failed_items=[
            {
                "name": r.get("name", "item"),
                "reason": r.get("error", "import_failed"),
                "retryable": True,
            }
            for r in failures
        ],
    )
