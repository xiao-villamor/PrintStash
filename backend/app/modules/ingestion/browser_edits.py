"""Account-owned browser-name edits, serialized with pairing claims.

The caller owns commit/rollback. History includes the pairing incarnation without
publishing its credential hash; credential use never changes the editing base.
"""

import hashlib
import re

from sqlalchemy import update
from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import BrowserDevice, User
from app.schemas.editing import EditContract, EditingBase, EditPrecondition


def editing_base(device: BrowserDevice) -> EditingBase:
    if device.id is None:
        raise ValueError("browser_edit_requires_persisted_device")
    identity = f"{device.database_epoch}:browser:{device.user_id}:{device.id}:{device.credential_hash}"
    return EditingBase(
        edit_epoch=hashlib.blake2b(identity.encode(), digest_size=16).hexdigest(),
        edit_version=device.edit_version,
    )


def _expected_base(
    device_id: int, precondition: EditPrecondition
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
        r'"browser-device-([1-9][0-9]*)-e([0-9a-f]{32})-v([1-9][0-9]*)"',
        precondition.if_match,
    )
    maximum = str(2**63 - 1)
    if (
        match is None
        or match[1] != str(device_id)
        or len(match[3]) > len(maximum)
        or (len(match[3]) == len(maximum) and match[3] > maximum)
    ):
        raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
    return EditingBase(edit_epoch=match[2], edit_version=int(match[3]))


def rename(
    session: Session,
    actor: User,
    device_id: int,
    name: str,
    precondition: EditPrecondition,
) -> BrowserDevice:
    # Pairing takes the same account lock before checking names or reusing rows.
    # Start with a write so SQLite cannot retain a stale pre-lock read snapshot.
    authority = session.execute(
        update(User)
        .where(col(User.id) == actor.id)
        .values(updated_at=col(User.updated_at))
        .returning(col(User.is_active), col(User.auth_version), col(User.deleted_at))
        .execution_options(synchronize_session=False)
    ).one_or_none()
    if (
        authority is None
        or not authority.is_active
        or authority.deleted_at is not None
        or authority.auth_version != actor.auth_version
    ):
        raise OperationError(
            "browser_device_permission_denied", kind=ErrorKind.FORBIDDEN
        )
    device = session.exec(
        select(BrowserDevice)
        .where(
            col(BrowserDevice.id) == device_id,
            col(BrowserDevice.user_id) == actor.id,
        )
        .execution_options(populate_existing=True)
    ).first()
    if device is None:
        raise OperationError("browser_device_not_found", kind=ErrorKind.NOT_FOUND)
    base = _expected_base(device_id, precondition)
    if base is not None and base != editing_base(device):
        raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
    if device.revoked_at is not None:
        raise OperationError("browser_device_revoked", kind=ErrorKind.CONFLICT)
    duplicate = session.exec(
        select(BrowserDevice.id).where(
            col(BrowserDevice.user_id) == actor.id,
            col(BrowserDevice.id) != device_id,
            col(BrowserDevice.name) == name,
        )
    ).first()
    if duplicate is not None:
        raise OperationError("browser_device_name_in_use", kind=ErrorKind.CONFLICT)
    # The version predicate also arbitrates revocation, which does not need the
    # account lock. Update the name and version in one statement, including no-ops.
    accepted = session.execute(
        update(BrowserDevice)
        .where(
            col(BrowserDevice.id) == device_id,
            col(BrowserDevice.user_id) == actor.id,
            col(BrowserDevice.edit_version) == device.edit_version,
            col(BrowserDevice.revoked_at).is_(None),
        )
        .values(name=name, edit_version=col(BrowserDevice.edit_version) + 1)
        .returning(col(BrowserDevice.id))
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    if accepted is None:
        raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
    session.refresh(device)
    return device
