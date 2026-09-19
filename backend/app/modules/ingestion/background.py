"""OSS import jobs and short-lived review manifests, independent of HTTP routing."""

from __future__ import annotations

import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Generic, Optional, TypeVar

from starlette.concurrency import run_in_threadpool

from app.core.logging import get_logger
from app.db.session import SessionFactory
from app.modules.ingestion import import_resolvers, importer
from app.modules.ingestion.acquisition import AcquisitionJournal
from app.runtime.jobs import registry
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


MESH_SUFFIXES = {".stl", ".3mf", ".obj", ".step", ".stp"}


@dataclass
class _PendingModelFiles:
    page_url: str
    page_title: str
    owner_user_id: Optional[int]
    files: list[import_resolvers.ModelFile]
    created_at: float = field(default_factory=time.time)


@dataclass
class _PendingCollection:
    title: str
    target_collection: str
    owner_user_id: Optional[int]
    members: list[import_resolvers.CollectionMember]
    # Retained in the in-memory contract for backwards compatibility. MakerWorld
    # collection resolution is extension-only and this value is always ``None``.
    makerworld_cookie: Optional[str] = None
    created_at: float = field(default_factory=time.time)


T = TypeVar("T", _PendingModelFiles, _PendingCollection)


class _PendingRegistry(Generic[T]):
    """Typed adapter for durable private review manifests."""

    def __init__(self, kind: str = "model_files") -> None:
        self.kind = kind

    def add(self, pending: T) -> str:
        from app.modules.ingestion import review_manifests
        return review_manifests.save(self.kind, asdict(pending), pending.owner_user_id)

    def get(self, token: str) -> Optional[T]:
        from app.modules.ingestion import review_manifests
        payload = review_manifests.get(self.kind, token)
        if payload is None:
            return None
        if self.kind == "collection":
            payload["members"] = [import_resolvers.CollectionMember(**member) for member in payload["members"]]
            return _PendingCollection(**payload)
        payload["files"] = [import_resolvers.ModelFile(**file) for file in payload["files"]]
        return _PendingModelFiles(**payload)

    def pop(self, token: str) -> Optional[T]:
        from app.modules.ingestion import review_manifests
        pending = self.get(token)
        review_manifests.remove(self.kind, token)
        return pending


pending_model_files: _PendingRegistry[_PendingModelFiles] = _PendingRegistry()
pending_collections: _PendingRegistry[_PendingCollection] = _PendingRegistry("collection")


def _makerworld_cookie(override: Optional[str]) -> Optional[str]:
    """Ignore the deprecated server-side MakerWorld credential field."""
    del override
    return None


def collection_target(parent: Optional[str], title: str) -> str:
    """Nest a collection named after the source under the user's chosen parent."""
    base = (title or "").strip() or "Imported collection"
    if parent and parent.strip():
        return f"{parent.strip().rstrip('/')}/{base}"
    return base


async def _download_and_collect(download_url: str) -> list[tuple[Path, str]]:
    """Download one direct link; if it is a zip, extract every importable entry.

    Returns the staged ``(path, filename)`` tuples ready for ingestion (empty if
    the link is neither a model file nor an archive with importable entries).
    """
    staged, original_filename = await importer.download_to_staging(download_url)
    suffix = Path(original_filename).suffix.lower()
    if suffix == ".zip" or (
        zipfile.is_zipfile(staged)
        and suffix not in MESH_SUFFIXES
        and suffix not in GCODE_SUFFIXES
    ):
        try:
            entries = await run_in_threadpool(importer.inspect_archive, staged)
            names = [e.name for e in entries if e.file_type]
            extracted = await run_in_threadpool(
                importer.extract_selected, staged, names
            )
        finally:
            staged.unlink(missing_ok=True)
        return extracted
    if suffix not in MESH_SUFFIXES and suffix not in GCODE_SUFFIXES:
        staged.unlink(missing_ok=True)
        return []
    return [(staged, original_filename)]


async def _stage_members(
    members: list[import_resolvers.CollectionMember],
    *,
    makerworld_cookie: Optional[str],
) -> list[importer.ResolvedGroup]:
    """Resolve + download every collection member, isolating per-member failures."""
    groups: list[importer.ResolvedGroup] = []
    for member in members:
        group = importer.ResolvedGroup(source_url=member.page_url, title=member.title)
        try:
            link = (
                await import_resolvers.resolve_page_url(
                    member.page_url, makerworld_cookie=makerworld_cookie
                )
                or member.page_url
            )
            group.staged_files = await _download_and_collect(link)
            if not group.staged_files:
                group.error = "no_importable_files"
        except importer.ImportError_ as exc:
            group.error = str(exc)
        except Exception as exc:  # noqa: BLE001 — per-member boundary; continue
            logger.exception("collection member failed: %s", member.page_url)
            group.error = str(exc)
        groups.append(group)
    return groups


def manifest_from_pending(
    archive_id: str, pending: "importer.PendingArchive"
) -> ArchiveManifest:
    return ArchiveManifest(
        archive_id=archive_id,
        archive_name=pending.archive_name,
        entries=[
            ArchiveEntryRead(
                entry_id=e.entry_id,
                name=e.name,
                size_bytes=e.size_bytes,
                file_type=e.file_type,
                is_image=e.is_image,
            )
            for e in pending.entries
        ],
    )


def _stage_model_files_manifest(
    job_id: str,
    req: UrlIngestRequest,
    actor_user_id: int,
    listing: tuple[str, list[import_resolvers.ModelFile]],
) -> None:
    """Stash a multi-file model page and report a manifest job result."""
    page_title, files = listing
    token = pending_model_files.add(
        _PendingModelFiles(
            page_url=req.url,
            page_title=page_title,
            owner_user_id=actor_user_id,
            files=files,
        )
    )
    manifest = ModelFilesManifest(
        files_token=token,
        page_title=page_title,
        files=[
            ModelFileRead(
                file_id=f.file_id, name=f.name, file_type=f.file_type, size=f.size
            )
            for f in files
        ],
    )
    registry.update(
        job_id,
        state="completed",
        result={
            "kind": "model_files_manifest",
            **manifest.model_dump(),
            "collection": req.collection,
        },
    )


async def _handle_collection_url(
    *,
    job_id: str,
    req: UrlIngestRequest,
    actor_user_id: int,
    session_factory: SessionFactory,
) -> None:
    """Resolve a collection URL; either stage a review manifest or import all."""
    registry.update(job_id, state="running", stage="resolving")
    cookie = _makerworld_cookie(req.makerworld_cookie)
    journal = AcquisitionJournal(job_id, session_factory)
    saved = await run_in_threadpool(journal.read, "collection_listing")
    if saved is None:
        resolved = await import_resolvers.resolve_collection_url(
            req.url, makerworld_cookie=cookie
        )
        if not resolved:
            registry.update(job_id, state="failed", error="collection_resolve_failed")
            return
        title, members = resolved
        await run_in_threadpool(journal.save, "collection_listing",
            {"title": title, "members": [asdict(member) for member in members]})
    else:
        title = saved["title"]
        members = [import_resolvers.CollectionMember(**member) for member in saved["members"]]
    target = collection_target(req.collection, title)

    if req.review:
        token = pending_collections.add(
            _PendingCollection(
                title=title,
                target_collection=target,
                owner_user_id=actor_user_id,
                members=members,
                makerworld_cookie=cookie,
            )
        )
        manifest = CollectionManifest(
            collection_token=token,
            collection_name=title,
            target_collection=target,
            members=[
                CollectionMemberRead(
                    source_id=m.source_id, title=m.title, page_url=m.page_url
                )
                for m in members
            ],
        )
        registry.update(
            job_id,
            state="completed",
            result={"kind": "collection_manifest", **manifest.model_dump()},
        )
        return

    await run_collection_member_import(
        job_id=job_id, members=members, target_collection=target, tags=req.tags,
        actor_user_id=actor_user_id, session_factory=session_factory,
        makerworld_cookie=cookie,
    )


async def import_from_url(
    *,
    job_id: str,
    req: UrlIngestRequest,
    actor_user_id: int,
    session_factory: SessionFactory,
) -> None:
    """Background task: download a URL, then ingest it or stage it as an archive.

    If ``req.url`` is a collection, it fans out into many models (auto or review);
    a multi-file Printables page returns a file-selection manifest; otherwise a
    model *page* is resolved to a direct download link (the user-pasted page URL
    is still recorded as the model's ``source_url``).
    """
    try:
        registry.update(job_id, state="running", stage="resolving")
        if import_resolvers.classify_collection(req.url):
            await _handle_collection_url(
                job_id=job_id,
                req=req,
                actor_user_id=actor_user_id,
                session_factory=session_factory,
            )
            return
        # A Printables page with more than one file → let the user pick which.
        listing = await import_resolvers.list_model_files(req.url)
        if listing is not None and len(listing[1]) > 1:
            _stage_model_files_manifest(job_id, req, actor_user_id, listing)
            return
        journal = AcquisitionJournal(job_id, session_factory)
        download_url = await run_in_threadpool(journal.read, "direct_link")
        if download_url is None:
            download_url = (
                await import_resolvers.resolve_page_url(
                    req.url,
                    makerworld_cookie=_makerworld_cookie(req.makerworld_cookie),
                    thingiverse_cookie=req.thingiverse_cookie,
                )
                or req.url
            )
            await run_in_threadpool(journal.save, "direct_link", download_url)
        registry.update(job_id, stage="downloading")
        staged, original_filename = await journal.download("direct_download", download_url)
    except importer.ImportError_ as exc:
        registry.update(job_id, state="failed", error=str(exc))
        return
    except Exception as exc:  # noqa: BLE001 — network/IO boundary
        logger.exception("url import download failed: %s", req.url)
        registry.update(job_id, state="failed", error=str(exc))
        return

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
            registry.update(job_id, stage="inspecting", current_item=original_filename)
            entries = await run_in_threadpool(importer.inspect_archive, staged)
        except importer.ImportError_ as exc:
            staged.unlink(missing_ok=True)
            registry.update(job_id, state="failed", error=str(exc))
            return
        pending = importer.PendingArchive(
            path=staged,
            archive_name=original_filename,
            owner_user_id=actor_user_id,
            entries=entries,
            source_url=req.url,
        )
        archive_id = importer.archives.add(pending)
        manifest = manifest_from_pending(archive_id, pending)
        registry.update(
            job_id,
            state="completed",
            result={"kind": "archive_manifest", **manifest.model_dump()},
        )
        return

    if suffix not in MESH_SUFFIXES and suffix not in GCODE_SUFFIXES:
        # The URL resolved to something that isn't a model file or a .zip —
        # almost always a model *page* (HTML) rather than a direct download
        # link. Use a dedicated code so the UI can tell the user what to paste.
        staged.unlink(missing_ok=True)
        registry.update(job_id, state="failed", error="url_not_a_direct_file")
        return

    # Single direct file — ingest under the user's chosen collection. Offload
    # the (blocking, CPU-heavy) pipeline so the event loop stays free.
    await run_in_threadpool(
        importer.import_assets,
        job_id=job_id,
        staged_files=[(staged, original_filename)],
        collection=req.collection,
        tags=req.tags,
        source_url=req.url,
        actor_user_id=actor_user_id,
        session_factory=session_factory,
    )


async def inspect_uploaded_archive(
    *, job_id: str, staged: Path, original_filename: str, actor_user_id: int
) -> None:
    """Inspect an uploaded ZIP in background and return its selection manifest."""
    try:
        registry.update(
            job_id,
            state="running",
            stage="inspecting",
            current_item=original_filename,
        )
        entries = await run_in_threadpool(importer.inspect_archive, staged)
        pending = importer.PendingArchive(
            path=staged,
            archive_name=original_filename,
            owner_user_id=actor_user_id,
            entries=entries,
        )
        archive_id = importer.archives.add(pending)
        manifest = manifest_from_pending(archive_id, pending)
        registry.update(
            job_id,
            state="completed",
            processed=len(entries),
            total=len(entries),
            result={"kind": "archive_manifest", **manifest.model_dump()},
        )
    except importer.ImportError_ as exc:
        staged.unlink(missing_ok=True)
        registry.update(job_id, state="failed", error=str(exc), retryable=True)
    except Exception as exc:  # noqa: BLE001 — background IO boundary
        staged.unlink(missing_ok=True)
        logger.exception("archive inspection failed")
        registry.update(job_id, state="failed", error=str(exc), retryable=True)


async def run_file_selection_import(
    *,
    job_id: str,
    page_url: str,
    files: list[import_resolvers.ModelFile],
    collection: Optional[str],
    tags: Optional[str],
    actor_user_id: int,
    session_factory: SessionFactory,
) -> None:
    """Save each chosen download before acquiring the next source."""
    journal = AcquisitionJournal(job_id, session_factory)
    try:
        links = await run_in_threadpool(journal.read, "selected_links")
        if links is None:
            links = await import_resolvers.resolve_selected_download(page_url, files)
            await run_in_threadpool(journal.save, "selected_links", links)
        await _import_downloads(job_id=job_id,
            sources=[(page_url, "", link) for link in links], collection=collection,
            tags=tags, actor_user_id=actor_user_id, session_factory=session_factory)
    except Exception as exc:
        logger.exception("file selection import failed: %s", page_url)
        registry.update(job_id, state="failed", error=str(exc), retryable=True)


async def run_collection_member_import(
    *,
    job_id: str,
    members: list[import_resolvers.CollectionMember],
    target_collection: str,
    tags: Optional[str],
    actor_user_id: int,
    session_factory: SessionFactory,
    makerworld_cookie: Optional[str] = None,
) -> None:
    """Resolve and save one member at a time with durable per-member progress."""
    await _import_downloads(job_id=job_id,
        sources=[(member.page_url, member.title, None) for member in members],
        collection=target_collection, tags=tags, actor_user_id=actor_user_id,
        session_factory=session_factory, collection_import=True,
        makerworld_cookie=makerworld_cookie)


async def _import_downloads(
    *, job_id: str, sources: list[tuple[str, str, str | None]],
    collection: str | None, tags: str | None, actor_user_id: int,
    session_factory: SessionFactory, collection_import: bool = False,
    makerworld_cookie: str | None = None,
) -> None:
    """Bound acquisition to one download; child identities checkpoint saved bytes."""
    journal = AcquisitionJournal(job_id, session_factory)
    results: list[dict] = []
    registry.update(job_id, state="running", stage="downloading", total=len(sources))
    for index, (page_url, title, direct_link) in enumerate(sources):
        child_id = uuid.uuid5(uuid.NAMESPACE_URL, f"acquired:{job_id}:{index}:{page_url}").hex
        try:
            previous = await run_in_threadpool(registry.get, child_id)
            if previous is None or previous.state not in {"completed", "failed"}:
                from app.modules.ingestion.ingestion import require_ingestion_actor
                def authorize():
                    with session_factory.scoped_session() as session:
                        require_ingestion_actor(session, actor_user_id, collection=collection)
                await run_in_threadpool(authorize)
                link = await run_in_threadpool(journal.read, f"link:{index}")
                if link is None:
                    link = direct_link or await import_resolvers.resolve_page_url(page_url, makerworld_cookie=makerworld_cookie) or page_url
                    await run_in_threadpool(journal.save, f"link:{index}", link)
                staged, filename = await journal.download(f"download:{index}", link)
                suffix = Path(filename).suffix.lower()
                is_archive = suffix == ".zip" or (suffix not in MESH_SUFFIXES | GCODE_SUFFIXES and await run_in_threadpool(zipfile.is_zipfile, staged))
                if not is_archive and suffix not in MESH_SUFFIXES | GCODE_SUFFIXES:
                    raise importer.ImportError_("no_importable_files")
                await run_in_threadpool(registry.create, kind="ingest_batch", visible=False,
                    owner_user_id=actor_user_id, job_id=child_id)
                if is_archive:
                    entries = await run_in_threadpool(importer.inspect_archive, staged)
                    await run_in_threadpool(importer.import_archive, job_id=child_id,
                        archive_path=staged, names=[entry.name for entry in entries if entry.file_type],
                        collection=collection, tags=tags, source_url=page_url,
                        actor_user_id=actor_user_id, session_factory=session_factory)
                else:
                    await run_in_threadpool(importer.import_assets, job_id=child_id,
                        staged_files=[(staged, filename)], collection=collection, tags=tags,
                        source_url=page_url, actor_user_id=actor_user_id, session_factory=session_factory)
                previous = await run_in_threadpool(registry.get, child_id)
            items = (previous.result or {}).get("items", []) if previous else []
            if not items:
                items = [{"name": title or page_url, "error": previous.error if previous and previous.error else "no_importable_files"}]
            results.extend([{**item, "member": title} if collection_import else item for item in items])
        except Exception as exc:
            logger.exception("selected source import failed: %s", page_url)
            results.append({"name": title or page_url, "error": str(exc)})
        registry.update(job_id, processed=index + 1, progress=(index + 1) / max(len(sources), 1) * 100,
            succeeded=sum(bool(item.get("model_id")) for item in results))
    imported = [item for item in results if item.get("model_id")]
    failures = [item for item in results if item.get("error")]
    result = {"imported": len(imported), "total": len(results), "items": results}
    if collection_import:
        result.update(kind="collection_import", collection=collection)
    errors = {item["error"] for item in failures}
    await run_in_threadpool(registry.finish, job_id,
        state="completed" if imported else "failed",
        completion="partial" if failures and imported else "complete",
        model_id=imported[0]["model_id"] if imported else None,
        succeeded=len(imported), failed=len(failures), total=len(results), processed=len(results),
        deduplicated=sum(bool(item.get("deduplicated")) for item in imported),
        error=None if imported else next(iter(errors)) if len(errors) == 1 else "collection_import_failed" if collection_import else "no_importable_files",
        result=result, retryable=bool(failures),
        failed_items=[{"name": item.get("name", "item"), "reason": item["error"], "retryable": True} for item in failures])
    if not failures:
        from app.modules.ingestion.staging_leases import release_job_files
        def cleanup():
            with session_factory.scoped_session() as session:
                release_job_files(session, job_id)
                session.commit()
        await run_in_threadpool(cleanup)
