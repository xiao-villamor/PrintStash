"""Conditional local preset edits, including concurrent sync and hard recreation.

The caller owns commit/rollback. The public epoch binds database history to the
persisted row identity so a reused integer ID cannot inherit a deleted draft.
"""

import hashlib
import re
from enum import Enum

from sqlalchemy import update
from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import FilamentProfile, PrinterProfile, User
from app.schemas.editing import EditContract, EditingBase, EditPrecondition


class ProfileKind(str, Enum):
    FILAMENT = "filament-profile"
    PRINTER = "printer-profile"


def kind(profile: FilamentProfile | PrinterProfile) -> ProfileKind:
    return (
        ProfileKind.FILAMENT
        if isinstance(profile, FilamentProfile)
        else ProfileKind.PRINTER
    )


def editing_base(profile: FilamentProfile | PrinterProfile) -> EditingBase:
    if profile.id is None:
        raise ValueError("preset_edit_requires_persisted_row")
    identity = f"{profile.database_epoch}:{kind(profile).value}:{profile.id}:{profile.edit_identity}"
    return EditingBase(
        edit_epoch=hashlib.blake2b(identity.encode(), digest_size=16).hexdigest(),
        edit_version=profile.edit_version,
    )


def expected_base(
    profile: FilamentProfile | PrinterProfile, precondition: EditPrecondition
) -> EditingBase | None:
    if (
        precondition.contract is not None
        and precondition.contract != EditContract.CONDITIONAL_V1.value
    ):
        raise OperationError("edit_contract_invalid")
    if precondition.if_match is None:
        if precondition.contract is not None:
            raise OperationError(
                "edit_precondition_required", kind=ErrorKind.PRECONDITION_REQUIRED
            )
        return None
    match = re.fullmatch(
        r'"(filament-profile|printer-profile)-([1-9][0-9]*)-e([0-9a-f]{32})-v([1-9][0-9]*)"',
        precondition.if_match,
    )
    maximum = str(2**63 - 1)
    if (
        match is None
        or match[1] != kind(profile).value
        or match[2] != str(profile.id)
        or len(match[4]) > len(maximum)
        or (len(match[4]) == len(maximum) and match[4] > maximum)
    ):
        raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
    return EditingBase(edit_epoch=match[3], edit_version=int(match[4]))


def claim(
    session: Session,
    actor: User,
    profile: FilamentProfile | PrinterProfile,
    precondition: EditPrecondition,
) -> None:
    if profile.id is None:
        raise ValueError("preset_edit_requires_persisted_row")
    base = expected_base(profile, precondition)
    table = FilamentProfile if isinstance(profile, FilamentProfile) else PrinterProfile
    statement = update(table).where(col(table.id) == profile.id)
    if base is not None:
        statement = statement.where(col(table.edit_version) == base.edit_version)
    with session.no_autoflush:
        claimed = session.execute(
            statement.values(edit_version=col(table.edit_version) + 1)
            .returning(col(table.edit_version))
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        if claimed is None:
            raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
        session.refresh(profile)
        # Checked under the acquired write lock; any refusal rolls the claim back.
        if base is not None and editing_base(profile).edit_epoch != base.edit_epoch:
            raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
        authority = session.execute(
            select(
                col(User.is_active), col(User.is_superuser), col(User.auth_version)
            ).where(col(User.id) == actor.id)
        ).one_or_none()
        if (
            authority is None
            or not authority.is_active
            or not authority.is_superuser
            or authority.auth_version != actor.auth_version
        ):
            raise OperationError("preset_permission_denied", kind=ErrorKind.FORBIDDEN)
        if (
            isinstance(profile, FilamentProfile)
            and profile.spoolman_filament_id is not None
        ):
            raise OperationError("filament_profile_linked", kind=ErrorKind.CONFLICT)
