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

import json
import os
import stat
import sys
import tempfile
import uuid
from collections.abc import Callable, Generator, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Optional
from urllib.parse import unquote, urlparse

import httpx
from printstash_core.files import (
    ArchiveEntry,
    ArchiveLimits,
    ArchivePolicyError,
    publish_staged_file,
    safe_entry_name,
    verify_archive_contents,
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
from sqlalchemy.exc import SQLAlchemyError

from app.core.cancellation import checkpoint
from app.core.config import settings
from app.core.logging import get_logger
from app.core.url_safety import (
    PinnedTarget,
    UnsafeUrlError,
    pinned_transport,
    resolve_public_target,
)
from app.db.models import SUFFIX_TO_FILE_TYPE
from app.db.session import SessionFactory, get_session_factory
from app.modules.ingestion.ingestion import StagedArtifact, commit_staged_artifact
from app.modules.storage.capacity import CapacityManager, CapacityResource
from app.modules.work.contracts import JobContext, JobExecution

from . import scratch_windows
from .batch_contracts import (
    ArchiveSource,
    BatchCommitReference,
    EntryRecord,
    EntrySpec,
    InboxBatch,
    JobBatch,
    LocalSource,
    RemoteSource,
)
from .batch_session import BatchSession, StagedInput, import_failure_code

if TYPE_CHECKING:
    from app.modules.library.provenance import ProvenanceContext

logger = get_logger(__name__)

_GCODE_SUFFIXES = {".gcode", ".g", ".gco", ".bgcode"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
# Importable 3D suffixes are exactly the ones the vault knows how to ingest.
_IMPORTABLE_SUFFIXES = set(SUFFIX_TO_FILE_TYPE.keys())


class ImportError_(Exception):
    """Raised for user-facing import failures (bad URL, unsafe archive, ...)."""


class WindowReleaseError(ImportError_):
    """A disposable window could not be released before further materialization."""

    def __init__(self) -> None:
        super().__init__("batch_window_release_failed")


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


async def download_to_staging(
    url: str,
    *,
    window_max_bytes: int | None = None,
    window: scratch_windows.ScratchWindow | None = None,
    owner: scratch_windows.WindowOwner | None = None,
    session_factory: SessionFactory | None = None,
) -> tuple[Path, str]:
    """Download ``url`` into the staging dir, re-validating every redirect hop.

    Returns ``(staged_path, original_filename)``. Enforces ``max_upload_bytes``.
    """
    if window_max_bytes is not None and (
        type(window_max_bytes) is not int or window_max_bytes <= 0
    ):
        raise ValueError("invalid_batch_window_bytes")
    if window is None and isinstance(owner, scratch_windows.JobWindowOwner):
        # Retry cleanup precedes resolution and the first outbound request.
        scratch_windows.recover_prior_windows(
            owner, session_factory or get_session_factory()
        )
    current = url
    for _ in range(settings.url_import_max_redirects + 1):
        checkpoint(force=True)
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
                limit = settings.max_upload_bytes
                window_limited = window_max_bytes is not None and suffix != ".zip"
                if window_limited:
                    assert window_max_bytes is not None
                    limit = min(limit, window_max_bytes)
                owned = window is None
                active = window or scratch_windows.create_window(
                    kind=scratch_windows.WindowKind.DOWNLOAD,
                    max_bytes=limit,
                    owner=owner,
                    session_factory=session_factory,
                )
                if active.max_bytes < limit:
                    limit = active.max_bytes
                    window_limited = True
                staged = active.directory / f"{uuid.uuid4().hex}{suffix}"
                succeeded = False
                try:
                    fd, temp_name = tempfile.mkstemp(
                        prefix=".printstash-url-", dir=active.directory
                    )
                    temp = Path(temp_name)
                    written = 0
                    with os.fdopen(fd, "wb") as out:
                        async for chunk in resp.aiter_bytes(1024 * 1024):
                            checkpoint()
                            written += len(chunk)
                            if written > limit:
                                raise ImportError_(
                                    "batch_entry_too_large"
                                    if window_limited
                                    and limit <= settings.max_upload_bytes
                                    else "download_too_large"
                                )
                            out.write(chunk)
                        out.flush()
                        os.fsync(out.fileno())
                    checkpoint(force=True)
                    publish_staged_file(temp, staged)
                    active.seal(staged)
                    succeeded = True
                    if owned:
                        active.detach()
                    return staged, original_filename
                finally:
                    if owned and not succeeded:
                        primary = sys.exception()
                        try:
                            active.close()
                        except scratch_windows.WindowCleanupError as exc:
                            if primary is not None:
                                primary.add_note(f"scratch cleanup failed: {exc}")
                            else:
                                raise WindowReleaseError() from exc
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


def prepare_archive_for_review(
    path: Path,
    *,
    on_chunk: Callable[[], None],
    on_entry: Callable[[int, int], None],
) -> list[ArchiveEntry]:
    """Validate and decompress ZIP contents in the existing ingest Job."""
    entries = inspect_archive(path)
    try:
        verify_archive_contents(
            path,
            entries,
            max_entry_bytes=settings.max_archive_entry_mb * 1024 * 1024,
            on_chunk=on_chunk,
            on_entry=on_entry,
        )
    except ArchivePolicyError as exc:
        raise ImportError_(exc.code) from exc
    return entries


def extract_selected(path: Path, names: list[str]) -> list[tuple[Path, str]]:
    """Extract chosen 3D entries to staging. Returns [(staged_path, rel_name)].

    ``rel_name`` keeps the archive-relative path (e.g. ``Dragons/red.stl``) so
    importers that opt into ``nest_subdirs`` can mirror the folder layout into
    sub-collections; entries at the archive root have no separator and behave
    exactly as a bare filename did before.
    """
    checkpoint(force=True)
    max_entry = settings.max_archive_entry_mb * 1024 * 1024
    selected_names = set(names)
    peak_bytes = sum(
        entry.size_bytes
        for entry in inspect_archive(path)
        if entry.name in selected_names or entry.entry_id in selected_names
    )
    try:
        with CapacityManager(get_session_factory()).hold(
            f"archive-extract:{uuid.uuid4().hex}",
            [
                CapacityResource.for_path(
                    settings.incoming_dir, peak_bytes, role="archive extraction"
                )
            ],
        ):
            return extract_selected_archive_entries(
                path,
                names,
                staging_dir=settings.incoming_dir,
                max_entry_bytes=max_entry,
                importable_suffixes=_IMPORTABLE_SUFFIXES,
                on_chunk=checkpoint,
                on_entry=lambda: checkpoint(force=True),
            )
    except ArchivePolicyError as exc:
        raise ImportError_(exc.code) from exc


def selected_archive_entries(
    path: Path, names: Sequence[str]
) -> tuple[ArchiveEntry, ...]:
    """Validate the complete directory before selecting disposable outputs."""
    checkpoint(force=True)
    wanted = set(names)
    entries = tuple(
        entry
        for entry in inspect_archive(path)
        if (entry.name in wanted or entry.entry_id in wanted)
        and Path(entry.name).suffix.lower() in _IMPORTABLE_SUFFIXES
    )
    cap = settings.ingestion_batch_max_mb * 1024 * 1024
    if any(entry.size_bytes > cap for entry in entries):
        raise ImportError_("batch_entry_too_large")
    return entries


def archive_entry_specs(
    path: Path, names: Sequence[str], source_id: str
) -> tuple[EntrySpec, ...]:
    """Freeze logical archive entries independently of selection/window order."""
    return tuple(
        EntrySpec(
            json.dumps(["archive", source_id, entry.entry_id], separators=(",", ":")),
            entry.name.replace("\\", "/"),
            ArchiveSource(source_id, entry.entry_id),
            entry.size_bytes,
        )
        for entry in selected_archive_entries(path, names)
    )


def iter_archive_entries(
    path: Path,
    names: Sequence[str],
    *,
    skip_entry: Callable[[ArchiveEntry], bool] | None = None,
    owner: scratch_windows.WindowOwner | None = None,
    session_factory: SessionFactory | None = None,
) -> Generator[tuple[ArchiveEntry, tuple[Path, str]], None, None]:
    """Yield one expanded output with capacity retained until consumption/cleanup.

    Callers close the iterator on unwind. A durable source archive is never
    transferred to disposable cleanup. Sequential one-entry windows satisfy
    both configured bounds without staging the rest of the selection.
    """
    entries = selected_archive_entries(path, names)
    for entry in entries:
        checkpoint(force=True)
        if skip_entry is not None and skip_entry(entry):
            continue
        files: list[tuple[Path, str]] = []
        output_identity: tuple[int, int] | None = None
        try:
            with scratch_windows.open_window(
                kind=scratch_windows.WindowKind.ARCHIVE_ENTRY,
                max_bytes=max(entry.size_bytes, 1),
                owner=owner,
                session_factory=session_factory,
            ) as workspace:
                try:
                    files = extract_selected_archive_entries(
                        path,
                        [entry.name],
                        staging_dir=workspace.directory,
                        max_entry_bytes=entry.size_bytes,
                        importable_suffixes=_IMPORTABLE_SUFFIXES,
                        on_chunk=checkpoint,
                        on_entry=lambda: checkpoint(force=True),
                    )
                    if len(files) != 1:
                        raise ImportError_("archive_entry_missing")
                    workspace.seal(files[0][0])
                    metadata = files[0][0].lstat()
                    output_identity = (metadata.st_dev, metadata.st_ino)
                    checkpoint(force=True)
                    yield entry, files[0]
                finally:
                    for staged, _ in files:
                        _discard_staged_file(
                            staged,
                            strict=True,
                            expected_identity=output_identity,
                            session_factory=session_factory,
                        )
        except scratch_windows.WindowCleanupError as exc:
            raise WindowReleaseError() from exc
        except ArchivePolicyError as exc:
            raise ImportError_(exc.code) from exc


def _discard_staged_file(
    path: Path,
    *,
    strict: bool,
    expected_identity: tuple[int, int] | None = None,
    session_factory: SessionFactory | None = None,
) -> None:
    primary = sys.exception()
    try:
        disposition = scratch_windows.release_path(
            path, session_factory=session_factory
        )
        if disposition in (
            scratch_windows.ReleaseDisposition.RELEASED,
            scratch_windows.ReleaseDisposition.TRANSFERRED,
            scratch_windows.ReleaseDisposition.DEFERRED,
        ):
            return
        if disposition is scratch_windows.ReleaseDisposition.UNCERTAIN:
            raise OSError("scratch workspace identity or release is uncertain")
        if expected_identity is not None:
            current = path.lstat()
            if (
                not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != expected_identity
            ):
                raise OSError("owned staging output changed before release")
        path.unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    except (OSError, SQLAlchemyError) as exc:
        if strict:
            if primary is None:
                raise WindowReleaseError() from exc
            primary.add_note(f"batch_window_release_failed: {path.name}: {exc}")
        logger.warning("import staging cleanup failed: %s", path.name, exc_info=True)


def discard_staged_files(
    paths: Iterable[Path],
    *,
    strict: bool = False,
    session_factory: SessionFactory | None = None,
) -> None:
    """Release exact temporary sources transferred to an import operation.

    Archive/slot inputs with durable leases are never included. Strict window
    callers stop on release failure; an existing primary exception is preserved.
    Default cleanup remains best effort. Identity checks cover exclusively owned
    private staging and do not promise atomicity against an active renamer.
    """
    for path in paths:
        _discard_staged_file(path, strict=strict, session_factory=session_factory)


# ---------------------------------------------------------------------------
# Grouped import — each 3D file becomes its own Model under one Collection
# ---------------------------------------------------------------------------


def archive_collection_path(parent: Optional[str], archive_name: str) -> str:
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
    ingestion_key: str,
    provenance_context: ProvenanceContext | None = None,
    batch_commit: BatchCommitReference | None = None,
) -> Optional[dict]:
    """Commit one staged file as its own Artifact.

    Returns a result dict (``model_id``/``file_id``/``name`` on success, or
    ``name``/``error`` on failure), or ``None`` if the suffix is not importable
    (the caller records it as a skipped step).

    ``original_filename`` may carry an archive-relative path; only its basename
    is used for the suffix, model name, and stored filename. Callers that want
    the directory mirrored into a sub-collection derive that into ``collection``
    before calling (see ``import_assets``' ``nest_subdirs``). ``ingestion_key``
    makes a resubmitted import skip files a previous attempt already committed.
    """
    relative = original_filename.replace("\\", "/")
    original_filename = PurePosixPath(relative).name
    suffix = Path(original_filename).suffix.lower()
    resolved_name = model_name or Path(original_filename).stem
    file_type = SUFFIX_TO_FILE_TYPE.get(
        ".gcode" if suffix in _GCODE_SUFFIXES else suffix
    )
    if file_type is None:
        staged.unlink(missing_ok=True)
        return None
    try:
        outcome = commit_staged_artifact(
            StagedArtifact(
                staged_path=staged,
                original_filename=original_filename,
                model_name=resolved_name,
                file_type=file_type,
                collection=collection,
                tags=tags,
                source_url=source_url,
            ),
            ingestion_key=ingestion_key,
            actor_user_id=actor_user_id,
            session_factory=session_factory,
            provenance_context=provenance_context,
            batch_commit=batch_commit,
        )
    except Exception as exc:  # noqa: BLE001 — per-file boundary; continue
        logger.exception("import file failed: %s", original_filename)
        staged.unlink(missing_ok=True)
        return {"name": original_filename, "error": import_failure_code(exc)}
    staged.unlink(missing_ok=True)
    return {
        "model_id": outcome.model_id,
        "file_id": outcome.file_id,
        "name": original_filename,
        "deduplicated": outcome.deduplicated,
    }


def item_ingestion_key(job_id: str, name: str) -> str:
    """A stable per-file ingestion key within one Job (at most 64 characters)."""
    import hashlib

    digest = hashlib.sha256(name.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return f"{job_id[:40]}:{digest}"


def direct_entry_spec(
    source_id: str,
    selection_id: str,
    filename: str,
    size_bytes: int | None = None,
    *,
    result_key: str = "self",
    archive_entry_id: str | None = None,
) -> EntrySpec:
    """Plan a selected source before source bytes or an expiring URL are opened."""
    if not isinstance(selection_id, str) or not selection_id:
        raise ValueError("selection_id_required")
    if not isinstance(result_key, str) or not result_key:
        raise ValueError("result_key_required")
    descriptor = (
        ArchiveSource(source_id, archive_entry_id)
        if archive_entry_id is not None
        else RemoteSource(source_id)
    )
    identity = json.dumps(
        ["capture", source_id, selection_id, result_key, archive_entry_id],
        separators=(",", ":"),
    )
    return EntrySpec(identity, filename.replace("\\", "/"), descriptor, size_bytes)


def entry_spec(staged: StagedInput, *, source_id: str | None = None) -> EntrySpec:
    """Identify a direct-call source independently of staging path or execution order."""
    if isinstance(staged, StagedAsset):
        resolved = staged.resolved
        name = (
            staged.container_entry_path
            if staged.container_entry_path is not None
            else resolved.source_filename
        )
        return direct_entry_spec(
            resolved.source_item_id,
            staged.source_selection_id,
            name,
            result_key=staged.result_key,
        )
    else:
        _, name = staged
        name = name.replace("\\", "/")
        source = source_id if source_id is not None else "direct-staged-input"
        identity = json.dumps(["direct", source, name], separators=(",", ":"))
        descriptor = (
            RemoteSource(source) if source_id is not None else LocalSource(source)
        )
    # Direct APIs also accept missing confirmed paths on retry. Physical size
    # is therefore validated when an unfinished entry is actually consumed.
    return EntrySpec(identity, name, descriptor, None)


def begin_batch(
    *,
    job_context: JobContext,
    collection: str | None,
    tags: str | None,
    source_url: str | None,
    actor_user_id: int | None,
    session_factory: SessionFactory,
    model_name: str | None = None,
    nest_subdirs: bool = False,
    inbox_item_id: int | None = None,
    grouped: bool = False,
) -> BatchSession:
    """Build one session; materializers register identities before copying bytes."""
    owner = (
        InboxBatch(inbox_item_id)
        if inbox_item_id is not None
        else JobBatch(job_context.job_id)
    )

    def commit_entry(
        record: EntryRecord,
        staged: StagedInput,
        entry_source_url: str | None,
        member_title: str | None,
    ) -> dict[str, object] | None:
        if isinstance(staged, StagedAsset):
            path = staged.staged_path
            name = (
                staged.container_entry_path
                if staged.container_entry_path is not None
                else staged.resolved.source_filename
            )
            provenance = _provenance_context(
                staged=staged, inbox_item_id=inbox_item_id, actor_user_id=actor_user_id
            )
            actual_url = staged.resolved.member_url or entry_source_url or source_url
        else:
            path, name = staged
            provenance = None
            actual_url = (
                entry_source_url if entry_source_url is not None else source_url
            )
        if path.stat().st_size > settings.ingestion_batch_max_mb * 1024 * 1024:
            raise ImportError_("batch_entry_too_large")
        file_collection = collection
        if nest_subdirs:
            subdir = _safe_subdir(name)
            if subdir:
                base = (collection or "").rstrip("/")
                file_collection = f"{base}/{subdir}" if base else subdir
        reference = BatchCommitReference(
            owner,
            record.id,
            JobExecution(
                job_context.job_id, job_context.attempt, job_context.execution_epoch
            ),
        )
        return _ingest_one_file(
            path,
            name,
            collection=file_collection,
            tags=tags,
            source_url=actual_url,
            model_name=model_name,
            actor_user_id=actor_user_id,
            session_factory=session_factory,
            ingestion_key=record.ingestion_key,
            provenance_context=provenance,
            batch_commit=reference,
        )

    return BatchSession(
        job_context=job_context,
        owner=owner,
        session_factory=session_factory,
        commit_entry=commit_entry,
        grouped=grouped,
        collection=collection,
    )


def import_assets(
    *,
    job_context: JobContext,
    staged_files: Sequence[StagedInput],
    collection: Optional[str],
    tags: Optional[str],
    source_url: Optional[str],
    actor_user_id: Optional[int],
    session_factory: SessionFactory,
    model_name: Optional[str] = None,
    nest_subdirs: bool = False,
    inbox_item_id: int | None = None,
) -> None:
    """Adapt an already-staged finite plan to one durable batch execution."""
    try:
        specs = tuple(
            entry_spec(staged, source_id=source_url) for staged in staged_files
        )
        tuple_sources: dict[str, Path] = {}
        for spec, staged in zip(specs, staged_files, strict=True):
            if isinstance(staged, StagedAsset):
                continue
            path = staged[0].absolute()
            previous = tuple_sources.setdefault(spec.key, path)
            if previous != path:
                raise ImportError_("batch_source_identity_ambiguous")
        session = begin_batch(
            job_context=job_context,
            collection=collection,
            tags=tags,
            source_url=source_url,
            actor_user_id=actor_user_id,
            session_factory=session_factory,
            model_name=model_name.strip()
            if model_name and len(staged_files) == 1
            else None,
            nest_subdirs=nest_subdirs,
            inbox_item_id=inbox_item_id,
        )
        session.begin(len({spec.key for spec in specs}))
        session.register(specs)
        for index, (spec, staged) in enumerate(zip(specs, staged_files, strict=True)):
            old_key = item_ingestion_key(
                job_context.job_id, f"{index}:{spec.display_name}"
            )
            session.consume(spec, staged, legacy_keys=(old_key,))
        session.finish()
    finally:
        discard_staged_files(
            (
                staged.staged_path if isinstance(staged, StagedAsset) else staged[0]
                for staged in staged_files
            ),
            session_factory=session_factory,
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
    from app.modules.library.provenance import ProvenanceContext

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
    job_context: JobContext,
    groups: list[ResolvedGroup],
    collection: Optional[str],
    tags: Optional[str],
    actor_user_id: Optional[int],
    session_factory: SessionFactory,
) -> None:
    """Adapt staged groups without deriving new unit identity from group ordering."""
    try:
        session = begin_batch(
            job_context=job_context,
            collection=collection,
            tags=tags,
            source_url=None,
            actor_user_id=actor_user_id,
            session_factory=session_factory,
            grouped=True,
        )
        planned = []
        for group in groups:
            source = group.source_url if group.source_url is not None else group.title
            specs = tuple(
                entry_spec(staged, source_id=source) for staged in group.staged_files
            )
            if not specs:
                specs = (
                    EntrySpec(
                        json.dumps(["member", source], separators=(",", ":")),
                        group.title,
                        RemoteSource(source),
                        None,
                    ),
                )
            planned.append(specs)
        session.begin(len({spec.key for specs in planned for spec in specs}))
        session.register(tuple(spec for specs in planned for spec in specs))
        for group_index, (group, specs) in enumerate(zip(groups, planned, strict=True)):
            checkpoint(force=True)
            if not group.staged_files:
                session.record_failure(specs[0], group.error or "no_importable_files")
                continue
            for file_index, (spec, staged) in enumerate(
                zip(specs, group.staged_files, strict=True)
            ):
                old_key = item_ingestion_key(
                    job_context.job_id,
                    f"{group_index}:{file_index}:{spec.display_name}",
                )
                session.consume(
                    spec,
                    staged,
                    source_url=group.source_url,
                    member_title=group.title,
                    legacy_keys=(old_key,),
                )
        session.finish()
    finally:
        discard_staged_files(
            (path for group in groups for path, _ in group.staged_files),
            session_factory=session_factory,
        )
