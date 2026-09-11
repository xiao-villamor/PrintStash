"""HTTP boundary for independent, human-managed Model variations."""

from contextlib import contextmanager
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response, UploadFile
from fastapi import File as UploadFileParam
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session, select

from app.core.errors import ErrorKind, OperationError
from app.core.security import require_auth, require_user
from app.db.models import FileType, ModelFamily, User
from app.db.session import get_session
from app.modules.library.families import (
    access,
    bulk,
    canonical,
    covers,
    lifecycle,
    members,
    metadata,
    mutations,
)
from app.modules.library.model_views import family_browse, family_members
from app.modules.library.model_views.families import family_reads
from app.modules.media.source_cover_processing import MAX_SOURCE_COVER_BYTES
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_deletion import process_storage_delete_intents
from app.schemas.families import (
    FamilyBrowsePage,
    FamilyBulkCollection,
    FamilyBulkTags,
    FamilyCanonicalChange,
    FamilyCreate,
    FamilyMemberAdd,
    FamilyMemberMove,
    FamilyMemberPage,
    FamilyMemberRead,
    FamilyMemberSort,
    FamilyMemberUpdate,
    FamilyPageRead,
    FamilyRead,
    FamilyRestoreRead,
    FamilyUpdate,
    FamilyVersion,
)
from app.schemas.family_types import VariantRole
from app.schemas.models import ModelBatchResult, ModelFilters, ModelSort

from .family_filters import family_browse_filters

router = APIRouter(prefix="/families", tags=["families"])


@router.get("/{family_id}/cover")
def get_family_cover(
    family_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> Response:
    family = access.require(session, user, family_id)
    key = covers.uploaded_key(family)
    if key is None:
        raise OperationError("family_cover_not_found", kind=ErrorKind.NOT_FOUND)
    backend = get_backend()
    if not backend.exists(key):
        raise OperationError("family_cover_blob_missing", kind=ErrorKind.GONE)
    return Response(
        content=backend.read_bytes(key),
        media_type=family.cover_content_type or "image/webp",
        headers={
            "Cache-Control": "private, no-cache",
            "ETag": f'"family-cover-{family.id}-{family.cover_filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.put(
    "/{family_id}/cover",
    response_model=FamilyRead,
    dependencies=[Depends(require_auth)],
)
async def upload_family_cover(
    family_id: int,
    version: int = Query(..., gt=0),
    file: UploadFile = UploadFileParam(...),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> FamilyRead:
    access.require(session, user, family_id, edit=True)
    data = await file.read(MAX_SOURCE_COVER_BYTES + 1)
    family = covers.upload(session, user, family_id, version, data, file.content_type)
    process_storage_delete_intents()
    return family_reads(session, user, [family])[family_id]


@router.delete(
    "/{family_id}/cover",
    response_model=FamilyRead,
    dependencies=[Depends(require_auth)],
)
def remove_family_cover(
    family_id: int,
    version: int = Query(..., gt=0),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> FamilyRead:
    with _transaction(session):
        family = covers.remove(session, user, family_id, version)
        result = family_reads(session, user, [family])[family_id]
    process_storage_delete_intents()
    return result


@router.delete(
    "/{family_id}/purge", status_code=204, dependencies=[Depends(require_auth)]
)
def purge_family(
    family_id: int,
    version: int = Query(..., gt=0),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> Response:
    with _transaction(session):
        lifecycle.purge_family(session, user, family_id, version)
    process_storage_delete_intents()
    return Response(status_code=204)


@contextmanager
def _transaction(session: Session):
    try:
        yield
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise OperationError(
            "family_membership_conflict", kind=ErrorKind.CONFLICT
        ) from exc
    except OperationalError as exc:
        session.rollback()
        if "locked" in str(exc).lower() or "serialization" in str(exc).lower():
            raise OperationError(
                "family_revision_conflict", kind=ErrorKind.CONFLICT
            ) from exc
        raise
    except Exception:
        session.rollback()
        raise


@router.post(
    "",
    response_model=FamilyRead,
    status_code=201,
    dependencies=[Depends(require_auth)],
)
def create_family(
    data: FamilyCreate,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> FamilyRead:
    with _transaction(session):
        family = mutations.create(session, user, data)
        result = family_reads(session, user, [family])[int(family.id)]
    return result


@router.get("", response_model=FamilyPageRead)
def list_families(
    q: str | None = Query(None, max_length=255),
    collection_id: int | None = Query(None, gt=0),
    favorites: bool = Query(False),
    tag: list[str] = Query(default=[]),
    trashed: bool = Query(False),
    sort: ModelSort = Query(ModelSort.DATE_DESC),
    cursor: str | None = Query(None, max_length=1024),
    limit: int = Query(60, ge=1, le=200),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> FamilyPageRead:
    return family_browse.family_page(
        session,
        user,
        q=q,
        collection_id=collection_id,
        favorites=favorites,
        tags=tag,
        include_trashed=trashed,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )


@router.get("/browse", response_model=FamilyBrowsePage)
def browse_families(
    filters: ModelFilters = Depends(family_browse_filters),
    sort: ModelSort = Query(ModelSort.DATE_DESC),
    cursor: str | None = Query(None, max_length=1024),
    limit: int = Query(60, ge=1, le=200),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> FamilyBrowsePage:
    return family_browse.collapsed_page(
        session,
        user,
        filters=filters,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )


@router.get("/by-slug/{slug}", response_model=FamilyRead)
def get_family_by_slug(
    slug: str,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> FamilyRead:
    family_id = session.exec(
        select(ModelFamily.id).where(ModelFamily.slug == slug)
    ).first()
    if family_id is None:
        raise OperationError("family_not_found", kind=ErrorKind.NOT_FOUND)
    family = access.require(session, user, family_id)
    return family_reads(session, user, [family])[family_id]


@router.get(
    "/{family_id}",
    response_model=FamilyRead,
)
def get_family(
    family_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> FamilyRead:
    family = access.require(session, user, family_id)
    return family_reads(session, user, [family])[family_id]


@router.get("/{family_id}/members", response_model=FamilyMemberPage)
def list_members(
    family_id: int,
    q: str | None = Query(None, max_length=255),
    role: VariantRole | None = Query(None),
    file_type: list[FileType] = Query(default=[]),
    known_good: bool | None = Query(None),
    has_revisions: bool | None = Query(None),
    source: Literal["vault", "external"] | None = Query(None),
    sort: FamilyMemberSort = Query(FamilyMemberSort.ORDER),
    cursor: str | None = Query(None, max_length=1024),
    limit: int = Query(60, ge=1, le=200),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> FamilyMemberPage:
    return family_members.member_page(
        session,
        user,
        family_id,
        q=q,
        role=role,
        formats=file_type,
        known_good=known_good,
        has_revisions=has_revisions,
        source=source,
        sort=sort,
        cursor=cursor,
        limit=limit,
    )


@router.post(
    "/{family_id}/members",
    response_model=FamilyMemberRead,
    dependencies=[Depends(require_auth)],
)
def add_member(
    family_id: int,
    data: FamilyMemberAdd,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> FamilyMemberRead:
    with _transaction(session):
        member = members.add(session, user, family_id, data)
        result = FamilyMemberRead.model_validate(member, from_attributes=True)
    return result


@router.patch(
    "/{family_id}/members/{member_id}",
    response_model=FamilyMemberRead,
    dependencies=[Depends(require_auth)],
)
def update_member(
    family_id: int,
    member_id: int,
    data: FamilyMemberUpdate,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> FamilyMemberRead:
    with _transaction(session):
        member = members.update_member(session, user, family_id, member_id, data)
        result = FamilyMemberRead.model_validate(member, from_attributes=True)
    return result


@router.delete(
    "/{family_id}/members/{member_id}",
    status_code=204,
    dependencies=[Depends(require_auth)],
)
def detach_member(
    family_id: int,
    member_id: int,
    version: int = Query(gt=0),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> Response:
    with _transaction(session):
        members.detach(session, user, family_id, member_id, version)
    return Response(status_code=204)


@router.post(
    "/{family_id}/canonical",
    response_model=FamilyRead,
    dependencies=[Depends(require_auth)],
)
def change_canonical(
    family_id: int,
    data: FamilyCanonicalChange,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> FamilyRead:
    with _transaction(session):
        family = canonical.choose(session, user, family_id, data)
        result = family_reads(session, user, [family])[family_id]
    return result


@router.post(
    "/{family_id}/move-member",
    response_model=FamilyMemberRead,
    dependencies=[Depends(require_auth)],
)
def move_member(
    family_id: int,
    data: FamilyMemberMove,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> FamilyMemberRead:
    with _transaction(session):
        member = members.move(session, user, family_id, data)
        result = FamilyMemberRead.model_validate(member, from_attributes=True)
    return result


@router.delete("/{family_id}", status_code=204, dependencies=[Depends(require_auth)])
def trash_family(
    family_id: int,
    version: int = Query(gt=0),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> Response:
    with _transaction(session):
        lifecycle.trash_family(session, user, family_id, version)
    return Response(status_code=204)


@router.post(
    "/{family_id}/restore",
    response_model=FamilyRestoreRead,
    dependencies=[Depends(require_auth)],
)
def restore_family(
    family_id: int,
    data: FamilyVersion,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> FamilyRestoreRead:
    with _transaction(session):
        family, omitted = lifecycle.restore_family(
            session, user, family_id, data.version
        )
        result = FamilyRestoreRead(
            family=family_reads(session, user, [family])[family_id],
            omitted_member_ids=omitted,
        )
    return result


@router.patch(
    "/{family_id}", response_model=FamilyRead, dependencies=[Depends(require_auth)]
)
def update_family(
    family_id: int,
    data: FamilyUpdate,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> FamilyRead:
    with _transaction(session):
        family = metadata.update_metadata(session, user, family_id, data)
        result = family_reads(session, user, [family])[family_id]
    if (
        "cover_model_id" in data.model_fields_set
        or "cover_image_url" in data.model_fields_set
    ):
        process_storage_delete_intents()
    return result


@router.put("/{family_id}/star", status_code=204, dependencies=[Depends(require_auth)])
def star_family(
    family_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> Response:
    with _transaction(session):
        metadata.star(session, user, family_id, True)
    return Response(status_code=204)


@router.delete(
    "/{family_id}/star", status_code=204, dependencies=[Depends(require_auth)]
)
def unstar_family(
    family_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> Response:
    with _transaction(session):
        metadata.star(session, user, family_id, False)
    return Response(status_code=204)


@router.post(
    "/{family_id}/members/tags",
    response_model=ModelBatchResult,
    dependencies=[Depends(require_auth)],
)
def bulk_tags(
    family_id: int,
    data: FamilyBulkTags,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> ModelBatchResult:
    with _transaction(session):
        result = bulk.apply_tags(session, user, family_id, data)
    return result


@router.post(
    "/{family_id}/members/collection",
    response_model=ModelBatchResult,
    dependencies=[Depends(require_auth)],
)
def bulk_collection(
    family_id: int,
    data: FamilyBulkCollection,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> ModelBatchResult:
    with _transaction(session):
        result = bulk.move_collection(session, user, family_id, data)
    return result


@router.post(
    "/{family_id}/members/star",
    response_model=ModelBatchResult,
    dependencies=[Depends(require_auth)],
)
def bulk_star(
    family_id: int,
    data: FamilyVersion,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
) -> ModelBatchResult:
    with _transaction(session):
        result = bulk.star_visible(session, user, family_id, data.version)
    return result
