"""The bodies of the URL, archive and collection import Jobs.

Each function runs as one job step for Job ``job_id``, reports progress onto
that Job, and settles it. A review flow (a multi-file model page, a collection
in review mode, an archive) ends its Job with a manifest *and* records that
manifest on the Job's ingest request; the selection that follows presents the
Job id as its token, so any process can serve it and a restart loses nothing.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from printstash_core.files import ArchiveEntry
from starlette.concurrency import run_in_threadpool

from app.core.cancellation import OperationCancelled, cancellation_scope, checkpoint
from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import SessionFactory
from app.modules.ingestion import batch_store, import_resolvers, importer, requests
from app.modules.ingestion.batch_contracts import (
    ArchiveSource,
    CollectionSource,
    EntrySpec,
    RemoteSource,
)
from app.modules.ingestion.batch_session import BatchSession
from app.modules.storage.hashing import sha256_file
from app.modules.work.contracts import JobContext, JobOutcome
from app.modules.work.jobs import failure_of
from app.schemas.ingest import (
    ArchiveEntryRead,
    ArchiveManifest,
    CollectionManifest,
    CollectionMemberRead,
    ModelFileRead,
    ModelFilesManifest,
    UrlIngestRequest,
)

logger = get_logger(__name__)


GCODE_SUFFIXES = {".gcode", ".g", ".gco", ".bgcode"}
MESH_SUFFIXES = {".stl", ".3mf", ".obj", ".step", ".stp", ".dxf"}


def collection_target(parent: Optional[str], title: str) -> str:
    """Nest a collection named after the source under the user's chosen parent."""
    base = (title or "").strip() or "Imported collection"
    if parent and parent.strip():
        return f"{parent.strip().rstrip('/')}/{base}"
    return base


def _download_spec(source_id: str, name: str, *, member: bool = False) -> EntrySpec:
    return EntrySpec(
        json.dumps(["download", source_id], separators=(",", ":")),
        name,
        CollectionSource(source_id) if member else RemoteSource(source_id),
        None,
    )


def _confirmed_source(
    batch: BatchSession, source_id: str, *, member_title: str | None = None
) -> bool:
    """Use the durable expansion snapshot before reopening any remote source."""
    records = batch.source_entries(source_id)
    expanded = tuple(
        record
        for record in records
        if isinstance(record.spec.descriptor, ArchiveSource)
    )
    if expanded:
        records = expanded
    if not records or not all(record.published for record in records):
        return False
    batch.register(tuple(record.spec for record in records))
    for record in records:
        batch.decorate(record.spec, member_title=member_title)
        if batch.confirmed(record.spec) is None:
            raise RuntimeError("batch_confirmation_lost")
    return True


# Old requests identify commits by indexed keys, not provider source IDs. Keep
# this lookup local to one execution; certification requires actual source bytes.
LegacyCandidates = dict[str, list[tuple[str, str]]]


def _legacy_candidates(batch: BatchSession) -> LegacyCandidates:
    candidates: LegacyCandidates = {}
    for candidate in batch_store.legacy_candidates(
        batch.context, batch.owner, session_factory=batch.factory
    ):
        candidates.setdefault(candidate.original_filename, []).append(
            (candidate.ingestion_key, candidate.sha256)
        )
    return candidates


def _certified_legacy_keys(
    candidates: LegacyCandidates, staged: Path, filename: str
) -> tuple[str, ...]:
    name = Path(filename.replace("\\", "/")).name
    choices = candidates.get(name, ())
    if not choices:
        return ()
    digest = sha256_file(staged, on_chunk=checkpoint)
    matching = [key for key, sha256 in choices if sha256 == digest]
    if len(matching) > 1:
        raise importer.ImportError_("batch_source_identity_ambiguous")
    if not matching:
        return ()
    key = matching[0]
    candidates[name] = [
        (candidate, sha256) for candidate, sha256 in choices if candidate != key
    ]
    return (key,)


def _record_source_failure(
    batch: BatchSession, spec: EntrySpec, error_code: str
) -> None:
    """Settle each uncommitted expanded unit, retaining already published outputs."""
    expanded = tuple(
        record
        for record in batch.source_entries(spec.descriptor.source_id)
        if isinstance(record.spec.descriptor, ArchiveSource)
    )
    if not expanded:
        batch.record_failure(spec, error_code)
        return
    batch.register(tuple(record.spec for record in expanded))
    batch.supersede(spec)
    for record in expanded:
        if not record.published:
            batch.record_failure(record.spec, error_code)


def _consume_archive(
    batch: BatchSession,
    archive: Path,
    names: list[str],
    *,
    source_id: str,
    source_url: str | None,
    member_title: str | None = None,
    legacy_candidates: LegacyCandidates | None = None,
    source_spec: EntrySpec | None = None,
) -> None:
    """Freeze the expansion, then consume and release one entry at a time."""
    previous = tuple(
        record.spec
        for record in batch.source_entries(source_id)
        if isinstance(record.spec.descriptor, ArchiveSource)
    )
    specs = importer.archive_entry_specs(archive, names, source_id)
    if previous and {spec.key for spec in previous} != {spec.key for spec in specs}:
        raise importer.ImportError_("batch_snapshot_mismatch")
    batch.register(specs)
    if source_spec is not None:
        batch.supersede(source_spec)
    by_id = {
        spec.descriptor.entry_id: spec
        for spec in specs
        if isinstance(spec.descriptor, ArchiveSource)
    }
    legacy_keys = {
        spec.key: (
            importer.item_ingestion_key(
                batch.context.job_id, f"{index}:{spec.display_name}"
            ),
        )
        if legacy_candidates is None
        else ()
        for index, spec in enumerate(specs)
    }
    for spec in specs:
        batch.decorate(spec, member_title=member_title)
    with closing(
        importer.iter_archive_entries(
            archive,
            names,
            skip_entry=lambda entry: (
                batch.confirmed(
                    by_id[entry.entry_id],
                    legacy_keys=legacy_keys[by_id[entry.entry_id].key],
                )
                is not None
            ),
        )
    ) as window:
        for entry, staged in window:
            checkpoint(force=True)
            batch.consume(
                by_id[entry.entry_id],
                staged,
                source_url=source_url,
                member_title=member_title,
                legacy_keys=(
                    legacy_keys[by_id[entry.entry_id].key]
                    if legacy_candidates is None
                    else _certified_legacy_keys(
                        legacy_candidates, staged[0], entry.name
                    )
                ),
            )


async def _consume_download(
    batch: BatchSession,
    download_url: str,
    *,
    spec: EntrySpec,
    source_url: str | None,
    member_title: str | None = None,
    legacy_candidates: LegacyCandidates | None = None,
) -> None:
    """Classify one bounded download; source intent is already durable in its request."""
    checkpoint(force=True)
    if legacy_candidates is None:
        legacy_candidates = _legacy_candidates(batch)
    if _confirmed_source(batch, spec.descriptor.source_id, member_title=member_title):
        return
    staged, filename = await importer.download_to_staging(
        download_url, window_max_bytes=settings.ingestion_batch_max_mb * 1024 * 1024
    )
    try:
        checkpoint(force=True)
        suffix = Path(filename).suffix.lower()
        if suffix == ".zip" or (
            suffix not in MESH_SUFFIXES | GCODE_SUFFIXES and zipfile.is_zipfile(staged)
        ):
            entries = await run_in_threadpool(importer.inspect_archive, staged)
            names = [entry.name for entry in entries if entry.file_type]
            await run_in_threadpool(
                _consume_archive,
                batch,
                staged,
                names,
                source_id=spec.descriptor.source_id,
                source_url=source_url,
                member_title=member_title,
                legacy_candidates=legacy_candidates,
                source_spec=spec,
            )
        elif suffix in MESH_SUFFIXES | GCODE_SUFFIXES:
            batch.register((spec,))
            await run_in_threadpool(
                batch.consume,
                spec,
                (staged, filename),
                source_url=source_url,
                member_title=member_title,
                legacy_keys=_certified_legacy_keys(legacy_candidates, staged, filename),
            )
        else:
            batch.record_skipped(spec, "unsupported_file_type")
    finally:
        importer.discard_staged_files((staged,), strict=True)


def _entry_error(exc: Exception) -> str:
    # Policy errors carry stable codes; arbitrary exception text is display-only.
    return str(exc) if isinstance(exc, importer.ImportError_) else type(exc).__name__


def archive_manifest(
    archive_id: str, archive_name: str, entries: list[ArchiveEntry]
) -> ArchiveManifest:
    return ArchiveManifest(
        archive_id=archive_id,
        archive_name=archive_name,
        entries=[
            ArchiveEntryRead(
                entry_id=e.entry_id,
                name=e.name,
                size_bytes=e.size_bytes,
                file_type=e.file_type,
                is_image=e.is_image,
            )
            for e in entries
        ],
    )


def _record_archive(
    job_id: str,
    *,
    archive_name: str,
    entries: list[ArchiveEntry],
    source_url: str | None,
) -> ArchiveManifest:
    requests.store_manifest(
        job_id,
        "archive",
        {
            "archive_name": archive_name,
            "entries": [asdict(entry) for entry in entries],
            "source_url": source_url,
        },
    )
    return archive_manifest(job_id, archive_name, entries)


def _stage_model_files_manifest(
    job_context: JobContext,
    req: UrlIngestRequest,
    listing: tuple[str, list[import_resolvers.ModelFile]],
) -> None:
    """Record a multi-file model page's files and end the Job with the manifest."""
    job_id = job_context.job_id
    page_title, files = listing
    requests.store_manifest(
        job_id,
        "model_files",
        {
            "page_url": req.url,
            "page_title": page_title,
            "files": [asdict(f) for f in files],
        },
    )
    manifest = ModelFilesManifest(
        files_token=job_id,
        page_title=page_title,
        files=[
            ModelFileRead(
                file_id=f.file_id, name=f.name, file_type=f.file_type, size=f.size
            )
            for f in files
        ],
    )
    job_context.finish(
        JobOutcome.COMPLETED,
        result={
            "kind": "model_files_manifest",
            **manifest.model_dump(),
            "collection": req.collection,
        },
    )


async def _handle_collection_url(
    *,
    job_context: JobContext,
    req: UrlIngestRequest,
    actor_user_id: int,
    session_factory: SessionFactory,
) -> None:
    """Resolve a collection URL; either record a review manifest or import all."""
    job_id = job_context.job_id
    job_context.update(stage="resolving")
    checkpoint(force=True)
    with session_factory.scoped_session() as session:
        request = requests.load(session, job_id)
        frozen = json.loads(request.manifest_json)
    if isinstance(frozen, dict) and frozen.get("kind") == "collection_import_plan":
        resolved = (
            frozen["title"],
            [
                import_resolvers.CollectionMember(**member)
                for member in frozen["members"]
            ],
        )
    else:
        resolved = await import_resolvers.resolve_collection_url(req.url)
    if not resolved:
        job_context.finish(JobOutcome.FAILED, error="collection_resolve_failed")
        return
    title, members = resolved
    target = collection_target(req.collection, title)

    if req.review:
        requests.store_manifest(
            job_id,
            "collection",
            {
                "title": title,
                "target_collection": target,
                "members": [asdict(m) for m in members],
            },
        )
        manifest = CollectionManifest(
            collection_token=job_id,
            collection_name=title,
            target_collection=target,
            members=[
                CollectionMemberRead(
                    source_id=m.source_id, title=m.title, page_url=m.page_url
                )
                for m in members
            ],
        )
        job_context.finish(
            JobOutcome.COMPLETED,
            result={"kind": "collection_manifest", **manifest.model_dump()},
        )
        return

    # Freeze auto-discovered membership before the first source is opened.
    requests.store_manifest(
        job_id,
        "collection_import_plan",
        {
            "title": title,
            "target_collection": target,
            "members": [asdict(member) for member in members],
        },
    )
    await run_collection_member_import(
        job_context=job_context,
        members=members,
        target_collection=target,
        tags=req.tags,
        actor_user_id=actor_user_id,
        session_factory=session_factory,
    )


def _lease_archive(job_id: str, staged: Path, actor_user_id: int) -> None:
    """Make the URL Job the owner of the archive it downloaded for review."""
    from app.db.session import get_session_factory
    from app.modules.ingestion import staging_leases
    from app.modules.storage.hashing import sha256_file

    with get_session_factory().scoped_session() as session:
        staging_leases.create_job_lease(
            session,
            job_id=job_id,
            owner_user_id=actor_user_id,
            path=staged,
            size_bytes=staged.stat().st_size,
            sha256=sha256_file(staged),
            check_capacity=False,
        )
        session.commit()


async def import_from_url(
    *,
    job_context: JobContext,
    req: UrlIngestRequest,
    actor_user_id: int,
    session_factory: SessionFactory,
) -> None:
    """Download a URL, then ingest it or record it as an archive to review.

    If ``req.url`` is a collection, it fans out into many models (auto or review);
    a multi-file Printables page returns a file-selection manifest; otherwise a
    model *page* is resolved to a direct download link (the user-pasted page URL
    is still recorded as the model's ``source_url``).
    """
    job_id = job_context.job_id
    batch = importer.begin_batch(
        job_context=job_context,
        collection=req.collection,
        tags=req.tags,
        source_url=req.url,
        actor_user_id=actor_user_id,
        session_factory=session_factory,
    )
    batch.begin(None)
    if _confirmed_source(batch, req.url):
        batch.discovery_complete()
        batch.finish()
        return
    try:
        checkpoint(force=True)
        job_context.update(stage="resolving")
        if import_resolvers.classify_collection(req.url):
            await _handle_collection_url(
                job_context=job_context,
                req=req,
                actor_user_id=actor_user_id,
                session_factory=session_factory,
            )
            return
        # A Printables page with more than one file → let the user pick which.
        listing = await import_resolvers.list_model_files(req.url)
        if listing is not None and len(listing[1]) > 1:
            _stage_model_files_manifest(job_context, req, listing)
            return
        checkpoint(force=True)
        download_url = (
            await import_resolvers.resolve_page_url(
                req.url, thingiverse_cookie=req.thingiverse_cookie
            )
            or req.url
        )
        job_context.update(stage="downloading")
        checkpoint(force=True)
        staged, original_filename = await importer.download_to_staging(
            download_url, window_max_bytes=settings.ingestion_batch_max_mb * 1024 * 1024
        )
    except importer.WindowReleaseError:
        raise
    except importer.ImportError_ as exc:
        job_context.finish(JobOutcome.FAILED, error=failure_of(exc))
        return
    except Exception as exc:  # noqa: BLE001 — network/IO boundary
        logger.exception("url import download failed: %s", req.url)
        job_context.finish(JobOutcome.FAILED, error=failure_of(exc), retryable=True)
        return

    leased = False
    try:
        checkpoint(force=True)
        suffix = Path(original_filename).suffix.lower()
        # Treat anything that is actually a zip as an archive (handles missing/odd
        # extensions on direct download links). A .3mf is itself a zip container but
        # is a single model, so route it (and other known mesh/g-code suffixes) to
        # direct ingestion rather than the archive-manifest flow.
        if suffix == ".zip" or (
            zipfile.is_zipfile(staged)
            and suffix not in MESH_SUFFIXES
            and suffix not in GCODE_SUFFIXES
        ):
            try:
                job_context.update(stage="inspecting", current_item=original_filename)
                entries = await run_in_threadpool(importer.inspect_archive, staged)
            except importer.ImportError_ as exc:
                staged.unlink(missing_ok=True)
                job_context.finish(JobOutcome.FAILED, error=failure_of(exc))
                return
            checkpoint(force=True)
            await run_in_threadpool(_lease_archive, job_id, staged, actor_user_id)
            leased = True
            manifest = _record_archive(
                job_id,
                archive_name=original_filename,
                entries=entries,
                source_url=req.url,
            )
            job_context.finish(
                JobOutcome.COMPLETED,
                result={"kind": "archive_manifest", **manifest.model_dump()},
            )
            return

        if suffix not in MESH_SUFFIXES and suffix not in GCODE_SUFFIXES:
            # The URL resolved to something that isn't a model file or a .zip —
            # almost always a model *page* (HTML) rather than a direct download
            # link. Use a dedicated code so the UI can tell the user what to paste.
            staged.unlink(missing_ok=True)
            job_context.finish(JobOutcome.FAILED, error="url_not_a_direct_file")
            return

        spec = _download_spec(req.url, original_filename)
        batch.register((spec,))
        await run_in_threadpool(
            batch.consume,
            spec,
            (staged, original_filename),
            legacy_keys=(
                importer.item_ingestion_key(job_id, f"0:{original_filename}"),
            ),
        )
        batch.discovery_complete()
        batch.finish()

    finally:
        if not leased:
            importer.discard_staged_files((staged,), strict=True)


def inspect_uploaded_archive(
    *,
    job_context: JobContext,
    staged: Path,
    original_filename: str,
    cancelled: Callable[[], bool],
) -> None:
    """Decompress an owned ZIP for validation, then record its review manifest."""
    job_id = job_context.job_id
    try:
        with cancellation_scope(cancelled):
            checkpoint(force=True)
            job_context.update(stage="extracting", current_item=original_filename)

            def report_entry(processed: int, total: int) -> None:
                checkpoint(force=True)
                job_context.update(stage="extracting", processed=processed, total=total)

            entries = importer.prepare_archive_for_review(
                staged, on_chunk=checkpoint, on_entry=report_entry
            )
            checkpoint(force=True)
            importable_count = sum(entry.file_type is not None for entry in entries)
            if importable_count == 0:
                raise importer.ImportError_("no_importable_files")
            manifest = _record_archive(
                job_id, archive_name=original_filename, entries=entries, source_url=None
            )
            job_context.finish(
                JobOutcome.COMPLETED,
                processed=importable_count,
                total=importable_count,
                succeeded=importable_count,
                result={"kind": "archive_manifest", **manifest.model_dump()},
            )
    except OperationCancelled:
        return
    except importer.ImportError_ as exc:
        # Retain uncommitted input until lease expiry or explicit discard.
        # Deterministic refusal still suppresses automatic retries.
        job_context.finish(JobOutcome.FAILED, error=failure_of(exc), retryable=False)


def run_archive_selection(
    *,
    job_context: JobContext,
    archive: Path,
    archive_name: str,
    names: list[str],
    collection: Optional[str],
    tags: Optional[str],
    source_url: Optional[str],
    actor_user_id: int,
    session_factory: SessionFactory,
) -> None:
    """Consume the reviewed selection with one disposable expanded entry."""
    checkpoint(force=True)
    batch = importer.begin_batch(
        job_context=job_context,
        collection=importer.archive_collection_path(collection, archive_name),
        tags=tags,
        source_url=source_url,
        actor_user_id=actor_user_id,
        session_factory=session_factory,
        nest_subdirs=True,
    )
    batch.begin(None)
    source_id = f"archive:{job_context.job_id}"
    try:
        if not _confirmed_source(batch, source_id):
            _consume_archive(
                batch, archive, names, source_id=source_id, source_url=source_url
            )
        batch.discovery_complete()
        batch.finish()
    except importer.WindowReleaseError:
        raise
    except Exception as exc:  # noqa: BLE001 — one archive's expanded units
        code = _entry_error(exc)
        _record_source_failure(batch, _download_spec(source_id, archive_name), code)
        batch.discovery_complete()
        batch.finish(failure_code=code)


async def run_file_selection_import(
    *,
    job_context: JobContext,
    page_url: str,
    files: list[import_resolvers.ModelFile],
    collection: Optional[str],
    tags: Optional[str],
    actor_user_id: int,
    session_factory: SessionFactory,
) -> None:
    """Resolve selected identities once, then publish before downloading the next."""
    batch = importer.begin_batch(
        job_context=job_context,
        collection=collection,
        tags=tags,
        source_url=page_url,
        actor_user_id=actor_user_id,
        session_factory=session_factory,
    )
    batch.begin(None)
    legacy_candidates = _legacy_candidates(batch)
    try:
        checkpoint(force=True)
        bundle_id = json.dumps(
            [page_url, sorted(file.file_id for file in files)], separators=(",", ":")
        )
        if _confirmed_source(batch, bundle_id):
            batch.discovery_complete()
            batch.finish()
            return
        remaining = []
        for file in files:
            source_id = json.dumps([page_url, file.file_id], separators=(",", ":"))
            if not _confirmed_source(batch, source_id):
                remaining.append(file)
        if files and not remaining:
            batch.discovery_complete()
            batch.finish()
            return
        sources = await import_resolvers.resolve_selected_sources(page_url, remaining)
        for source in sources:
            checkpoint(force=True)
            if isinstance(source, import_resolvers.SelectedFileDownload):
                source_id = json.dumps(
                    [page_url, source.file.file_id], separators=(",", ":")
                )
                name = source.file.name
            else:
                source_id = json.dumps(
                    [page_url, sorted(file.file_id for file in source.files)],
                    separators=(",", ":"),
                )
                name = "Selected archive"
            spec = _download_spec(source_id, name)
            try:
                await _consume_download(
                    batch,
                    source.url,
                    spec=spec,
                    source_url=page_url,
                    legacy_candidates=legacy_candidates,
                )
            except importer.WindowReleaseError:
                raise
            except Exception as exc:  # noqa: BLE001 — isolate one source, preserve cancellation
                _record_source_failure(batch, spec, _entry_error(exc))
        batch.discovery_complete()
        batch.finish()
    except importer.WindowReleaseError:
        raise
    except importer.ImportError_ as exc:
        job_context.finish(JobOutcome.FAILED, error=failure_of(exc))
    except Exception as exc:  # noqa: BLE001 — resolver/network boundary
        logger.exception("file selection import failed")
        job_context.finish(JobOutcome.FAILED, error=failure_of(exc), retryable=True)


async def run_collection_member_import(
    *,
    job_context: JobContext,
    members: list[import_resolvers.CollectionMember],
    target_collection: str,
    tags: Optional[str],
    actor_user_id: int,
    session_factory: SessionFactory,
) -> None:
    """Keep discovery totals unknown while publishing each resolved member."""
    batch = importer.begin_batch(
        job_context=job_context,
        collection=target_collection,
        tags=tags,
        source_url=None,
        actor_user_id=actor_user_id,
        session_factory=session_factory,
        grouped=True,
    )
    batch.begin(None)
    legacy_candidates = _legacy_candidates(batch)
    for member in members:
        checkpoint(force=True)
        source_id = json.dumps(
            [member.page_url, member.source_id], separators=(",", ":")
        )
        spec = _download_spec(source_id, member.title, member=True)
        try:
            if _confirmed_source(batch, source_id, member_title=member.title):
                continue
            link = (
                await import_resolvers.resolve_page_url(member.page_url)
                or member.page_url
            )
            checkpoint(force=True)
            await _consume_download(
                batch,
                link,
                spec=spec,
                source_url=member.page_url,
                member_title=member.title,
                legacy_candidates=legacy_candidates,
            )
        except importer.WindowReleaseError:
            raise
        except Exception as exc:  # noqa: BLE001 — isolate one member, preserve cancellation
            logger.exception("collection member import failed")
            _record_source_failure(batch, spec, _entry_error(exc))
    batch.discovery_complete()
    batch.finish()
