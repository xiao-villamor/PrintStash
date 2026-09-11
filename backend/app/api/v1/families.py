"""HTTP boundary for independent, human-managed Model variations."""

from contextlib import contextmanager

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlmodel import Session

from app.core.errors import ErrorKind, OperationError
from app.core.security import require_auth, require_user
from app.db.models import User
from app.db.session import get_session
from app.modules.library.families import (
    access,
    canonical,
    lifecycle,
    members,
    metadata,
    mutations,
)
from app.modules.library.model_views.families import family_reads
from app.schemas.families import (
    FamilyCanonicalChange,
    FamilyCreate,
    FamilyMemberAdd,
    FamilyMemberMove,
    FamilyMemberRead,
    FamilyMemberUpdate,
    FamilyRead,
    FamilyRestoreRead,
    FamilyUpdate,
    FamilyVersion,
)

router = APIRouter(prefix="/families", tags=["families"])


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
