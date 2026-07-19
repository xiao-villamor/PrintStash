from __future__ import annotations

import json
import shutil
import zipfile
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import HTTPException
from sqlmodel import Session, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import (
    BackgroundJob,
    Collection,
    CollectionRole,
    InboxItem,
    InboxItemState,
    User,
)
from app.db.session import SessionFactory, get_session_factory
from app.schemas.inbox import InboxItemCreate, InboxItemRead, InboxItemUpdate
from app.services import import_resolvers, importer, rbac
from app.services.jobs import registry, safe_error, safe_item

_SECRET_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "credential",
    "key",
    "password",
    "secret",
    "signature",
    "sig",
    "token",
}


def sanitize_source_url(value: str) -> str:
    raw = value.strip()
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("url_invalid")
    if parts.username or parts.password:
        raise ValueError("url_credentials_not_allowed")
    query = urlencode(
        [
            (key, val)
            for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in _SECRET_QUERY_KEYS
        ],
        doseq=True,
    )
    host = parts.hostname
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme.lower(), host, parts.path, query, ""))


def _json_dict(value: str) -> dict:
    try:
        result = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return result if isinstance(result, dict) else {}


def requested_tags(value: str) -> list[str]:
    try:
        result = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in result] if isinstance(result, list) else []


def read(row: InboxItem) -> InboxItemRead:
    return InboxItemRead(
        id=row.id,
        owner_user_id=row.owner_user_id,
        source_kind=row.source_kind,
        source_url=row.source_url,
        display_title=row.display_title,
        source_hostname=row.source_hostname,
        state=row.state,
        manifest=_json_dict(row.manifest_json),
        target_collection_id=row.target_collection_id,
        requested_tags=requested_tags(row.requested_tags_json),
        background_job_id=row.background_job_id,
        resulting_model_id=row.resulting_model_id,
        error_code=row.error_code,
        retryable=row.retryable,
        attempt_count=row.attempt_count,
        created_at=row.created_at,
        updated_at=row.updated_at,
        completed_at=row.completed_at,
    )


def require_visible(session: Session, user: User, item_id: int) -> InboxItem:
    row = session.get(InboxItem, item_id)
    if row is None or (not user.is_superuser and row.owner_user_id != user.id):
        raise HTTPException(status_code=404, detail="pending_import_not_found")
    return row


def list_visible(session: Session, user: User, *, include_completed: bool = True) -> list[InboxItemRead]:
    stmt = select(InboxItem)
    if not user.is_superuser:
        stmt = stmt.where(InboxItem.owner_user_id == user.id)
    if not include_completed:
        stmt = stmt.where(InboxItem.state != InboxItemState.COMPLETED)
    rows = session.exec(
        stmt.order_by(InboxItem.updated_at.desc(), InboxItem.id.desc())  # type: ignore[attr-defined]
    ).all()
    return [read(row) for row in rows]


def prune_history(retention_days: int = 30) -> int:
    """Bound terminal capture history and remove its managed staging directory."""
    cutoff = utcnow() - timedelta(days=retention_days)
    with get_session_factory().scoped_session() as session:
        rows = session.exec(
            select(InboxItem).where(
                InboxItem.state.in_((InboxItemState.COMPLETED, InboxItemState.DISMISSED)),  # type: ignore[attr-defined]
                InboxItem.updated_at < cutoff,
            )
        ).all()
        for row in rows:
            if row.id is not None:
                shutil.rmtree(settings.incoming_dir / "inbox" / str(row.id), ignore_errors=True)
            session.delete(row)
        session.commit()
        return len(rows)


def _require_target(session: Session, user: User, collection_id: int | None) -> None:
    if collection_id is None:
        if not user.is_superuser:
            raise HTTPException(status_code=403, detail="root_collection_admin_required")
        return
    rbac.require_collection_role(session, user, collection_id, CollectionRole.EDIT)


def create(session: Session, user: User, payload: InboxItemCreate) -> InboxItem:
    source_url = sanitize_source_url(payload.url)
    importer.validate_public_url(source_url)
    if payload.collection_id is not None:
        _require_target(session, user, payload.collection_id)
    title = safe_item(payload.title) if payload.title else None
    row = InboxItem(
        owner_user_id=user.id,
        source_kind=payload.source_kind,
        source_url=source_url,
        source_hostname=urlsplit(source_url).hostname,
        display_title=title,
        target_collection_id=payload.collection_id,
        requested_tags_json=json.dumps(payload.tags[:100]),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def update(session: Session, user: User, row: InboxItem, payload: InboxItemUpdate) -> InboxItem:
    if row.state in {InboxItemState.IMPORTING, InboxItemState.COMPLETED, InboxItemState.DISMISSED}:
        raise HTTPException(status_code=409, detail="pending_import_not_editable")
    if payload.title is not None:
        row.display_title = safe_item(payload.title)
    if "collection_id" in payload.model_fields_set:
        _require_target(session, user, payload.collection_id)
        row.target_collection_id = payload.collection_id
    if payload.tags is not None:
        row.requested_tags_json = json.dumps(payload.tags[:100])
    if payload.selected_ids is not None:
        manifest = _json_dict(row.manifest_json)
        manifest["selected_ids"] = payload.selected_ids[:500]
        row.manifest_json = json.dumps(manifest, separators=(",", ":"))
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _managed_staging(item_id: int, source: Path) -> Path:
    directory = settings.incoming_dir / "inbox" / str(item_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"source{source.suffix.lower()}"
    shutil.move(str(source), target)
    return target


async def resolve(item_id: int) -> None:
    with get_session_factory().scoped_session() as session:
        row = session.get(InboxItem, item_id)
        if row is None or row.state not in {
            InboxItemState.CAPTURED,
            InboxItemState.FAILED,
        }:
            return
        row.state = InboxItemState.RESOLVING
        row.error_code = None
        row.retryable = False
        row.attempt_count += 1
        row.updated_at = utcnow()
        session.add(row)
        session.commit()
        source_url = row.source_url

    try:
        if not source_url:
            raise importer.ImportError_("url_required")
        if import_resolvers.classify_collection(source_url):
            resolved = await import_resolvers.resolve_collection_url(source_url)
            if not resolved:
                raise importer.ImportError_("collection_resolve_failed")
            title, members = resolved
            manifest = {
                "kind": "collection",
                "title": safe_item(title),
                "members": [
                    {
                        "id": member.source_id,
                        "title": safe_item(member.title),
                        "page_url": sanitize_source_url(member.page_url),
                    }
                    for member in members
                ],
            }
        else:
            listing = await import_resolvers.list_model_files(source_url)
            if listing is not None:
                title, files = listing
                manifest = {
                    "kind": "model_files",
                    "title": safe_item(title),
                    "files": [
                        {
                            "id": item.file_id,
                            "name": safe_item(item.name),
                            "file_type": item.file_type,
                            "size": item.size,
                        }
                        for item in files
                    ],
                }
            else:
                download_url = await import_resolvers.resolve_page_url(source_url) or source_url
                staged, filename = await importer.download_to_staging(download_url)
                suffix = Path(filename).suffix.lower()
                if suffix == ".zip" or (zipfile.is_zipfile(staged) and suffix != ".3mf"):
                    entries = importer.inspect_archive(staged)
                    managed = _managed_staging(item_id, staged)
                    manifest = {
                        "kind": "archive",
                        "title": safe_item(filename),
                        "entries": [
                            {
                                "id": entry.name,
                                "name": entry.name,
                                "size": entry.size_bytes,
                                "file_type": entry.file_type,
                            }
                            for entry in entries
                            if entry.file_type
                        ],
                    }
                else:
                    staged.unlink(missing_ok=True)
                    managed = None
                    manifest = {"kind": "direct", "title": safe_item(filename)}
        with get_session_factory().scoped_session() as session:
            row = session.get(InboxItem, item_id)
            if row is None:
                return
            row.manifest_json = json.dumps(manifest, separators=(",", ":"))
            row.display_title = row.display_title or manifest.get("title")
            if manifest.get("kind") == "archive":
                row.staging_key = str(managed)
            row.state = InboxItemState.REVIEW
            row.updated_at = utcnow()
            session.add(row)
            session.commit()
    except Exception as exc:
        with get_session_factory().scoped_session() as session:
            row = session.get(InboxItem, item_id)
            if row is not None:
                row.state = InboxItemState.FAILED
                row.error_code = safe_error(str(exc)) or "resolve_failed"
                row.retryable = True
                row.updated_at = utcnow()
                session.add(row)
                session.commit()


async def _download_assets(url: str) -> list[tuple[Path, str]]:
    download_url = await import_resolvers.resolve_page_url(url) or url
    staged, name = await importer.download_to_staging(download_url)
    suffix = Path(name).suffix.lower()
    if suffix == ".zip" or (zipfile.is_zipfile(staged) and suffix != ".3mf"):
        entries = importer.inspect_archive(staged)
        selected = [entry.name for entry in entries if entry.file_type]
        extracted = importer.extract_selected(staged, selected)
        staged.unlink(missing_ok=True)
        return extracted
    return [(staged, name)]


async def run_import(item_id: int, selected_ids: list[str], session_factory: SessionFactory) -> None:
    with session_factory.scoped_session() as session:
        row = session.get(InboxItem, item_id)
        if row is None or row.state != InboxItemState.REVIEW:
            return
        user = session.get(User, row.owner_user_id)
        if user is None:
            return
        _require_target(session, user, row.target_collection_id)
        collection = session.get(Collection, row.target_collection_id) if row.target_collection_id else None
        collection_path = collection.path if collection else None
        tags = ",".join(requested_tags(row.requested_tags_json)) or None
        manifest = _json_dict(row.manifest_json)
        selected = selected_ids or manifest.get("selected_ids") or []
        row.state = InboxItemState.IMPORTING
        row.error_code = None
        row.retryable = False
        row.updated_at = utcnow()
        job_id = registry.create(owner_user_id=row.owner_user_id)
        job_row = session.get(BackgroundJob, job_id)
        if job_row is not None:
            job_row.kind = "pending_import"
            job_row.replay_safe = True
            session.add(job_row)
        row.background_job_id = job_id
        session.add(row)
        session.commit()
        source_url = row.source_url
        owner_id = row.owner_user_id
        staging_key = row.staging_key

    try:
        assets: list[tuple[Path, str]] = []
        kind = manifest.get("kind")
        if kind == "archive":
            entries = [entry.get("id") for entry in manifest.get("entries", [])]
            wanted = [item for item in selected if item in entries] or entries
            if not staging_key or not Path(staging_key).exists():
                raise importer.ImportError_("staging_expired")
            assets = importer.extract_selected(Path(staging_key), wanted)
        elif kind == "model_files":
            files_by_id = {item["id"]: item for item in manifest.get("files", [])}
            wanted = [item for item in selected if item in files_by_id] or list(files_by_id)
            chosen = [
                import_resolvers.ModelFile(
                    file_id=item_id_value,
                    name=files_by_id[item_id_value]["name"],
                    file_type=files_by_id[item_id_value]["file_type"],
                    size=files_by_id[item_id_value].get("size"),
                )
                for item_id_value in wanted
            ]
            links = await import_resolvers.resolve_selected_download(source_url, chosen)
            for link in links:
                assets.extend(await _download_assets(link))
        elif kind == "collection":
            members = {item["id"]: item for item in manifest.get("members", [])}
            wanted = [item for item in selected if item in members] or list(members)
            for member_id in wanted:
                assets.extend(await _download_assets(members[member_id]["page_url"]))
        else:
            assets = await _download_assets(source_url)
        importer.import_assets(
            job_id=job_id,
            staged_files=assets,
            collection=collection_path,
            tags=tags,
            source_url=source_url,
            actor_user_id=owner_id,
            session_factory=session_factory,
        )
        job = registry.get(job_id)
        with session_factory.scoped_session() as session:
            row = session.get(InboxItem, item_id)
            if row is None:
                return
            if job and job.state == "completed" and job.model_id:
                row.state = InboxItemState.COMPLETED
                row.resulting_model_id = job.model_id
                row.completed_at = utcnow()
                row.retryable = False
                if row.staging_key:
                    Path(row.staging_key).unlink(missing_ok=True)
                    row.staging_key = None
            else:
                row.state = InboxItemState.FAILED
                row.error_code = job.error if job else "import_failed"
                row.retryable = True
            row.updated_at = utcnow()
            session.add(row)
            session.commit()
    except Exception as exc:
        registry.update(job_id, state="failed", error=str(exc), retryable=True)
        with session_factory.scoped_session() as session:
            row = session.get(InboxItem, item_id)
            if row is not None:
                row.state = InboxItemState.FAILED
                row.error_code = safe_error(str(exc)) or "import_failed"
                row.retryable = True
                row.updated_at = utcnow()
                session.add(row)
                session.commit()


def retry(session: Session, row: InboxItem) -> InboxItem:
    if row.state != InboxItemState.FAILED or not row.retryable:
        raise HTTPException(status_code=409, detail="pending_import_not_retryable")
    manifest = _json_dict(row.manifest_json)
    row.state = InboxItemState.REVIEW if manifest else InboxItemState.CAPTURED
    row.error_code = None
    row.retryable = False
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def dismiss(session: Session, row: InboxItem) -> None:
    if row.state == InboxItemState.IMPORTING:
        raise HTTPException(status_code=409, detail="pending_import_busy")
    if row.staging_key:
        path = Path(row.staging_key)
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass
        row.staging_key = None
    row.state = InboxItemState.DISMISSED
    row.updated_at = utcnow()
    session.add(row)
    session.commit()


def reconcile_interrupted_items() -> int:
    with get_session_factory().scoped_session() as session:
        rows = session.exec(
            select(InboxItem).where(
                InboxItem.state.in_((InboxItemState.RESOLVING, InboxItemState.IMPORTING))  # type: ignore[attr-defined]
            )
        ).all()
        for row in rows:
            job = registry.get(row.background_job_id) if row.background_job_id else None
            if (
                row.state == InboxItemState.IMPORTING
                and job is not None
                and job.state == "completed"
                and job.model_id is not None
            ):
                row.state = InboxItemState.COMPLETED
                row.resulting_model_id = job.model_id
                row.completed_at = job.finished_at or utcnow()
                row.retryable = False
                row.error_code = None
                row.updated_at = utcnow()
                session.add(row)
                continue
            row.state = InboxItemState.FAILED
            row.error_code = "import_interrupted"
            row.retryable = True
            row.updated_at = utcnow()
            session.add(row)
        session.commit()
        return len(rows)
