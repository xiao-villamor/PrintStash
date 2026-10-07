"""Notification edit claims share the caller's transaction with validation and save.

Independent channel incarnations and the global switch have separate versions.
Reads pair values with history; claims recheck current administrator authority.
"""

import hashlib
import re

from sqlalchemy import update
from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import LibraryRevision, NotificationChannel, SystemConfig, User
from app.schemas.editing import EditContract, EditingBase, EditPrecondition


def channel_base(channel: NotificationChannel) -> EditingBase:
    if channel.id is None:
        raise ValueError("notification_edit_requires_persisted_channel")
    identity = (
        f"{channel.database_epoch}:notification:{channel.id}:{channel.edit_identity}"
    )
    return EditingBase(
        edit_epoch=hashlib.blake2b(identity.encode(), digest_size=16).hexdigest(),
        edit_version=channel.edit_version,
    )


def expected_base(aggregate: str, precondition: EditPrecondition) -> EditingBase | None:
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
        r'"' + re.escape(aggregate) + r'-e([0-9a-f]{32})-v([1-9][0-9]*)"',
        precondition.if_match,
    )
    maximum = str(2**63 - 1)
    if (
        match is None
        or len(match[2]) > len(maximum)
        or (len(match[2]) == len(maximum) and match[2] > maximum)
    ):
        raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
    return EditingBase(edit_epoch=match[1], edit_version=int(match[2]))


def _require_authority(session: Session, actor: User) -> None:
    authority = session.execute(
        select(
            col(User.is_active),
            col(User.is_superuser),
            col(User.auth_version),
            col(User.deleted_at),
        ).where(col(User.id) == actor.id)
    ).one_or_none()
    if (
        authority is None
        or not authority.is_active
        or not authority.is_superuser
        or authority.deleted_at is not None
        or authority.auth_version != actor.auth_version
    ):
        raise OperationError("notification_permission_denied", kind=ErrorKind.FORBIDDEN)


def claim_channel(
    session: Session,
    actor: User,
    channel: NotificationChannel,
    precondition: EditPrecondition,
) -> None:
    if channel.id is None:
        raise ValueError("notification_edit_requires_persisted_channel")
    base = expected_base(f"notification-channel-{channel.id}", precondition)
    statement = update(NotificationChannel).where(
        col(NotificationChannel.id) == channel.id
    )
    if base is not None:
        statement = statement.where(
            col(NotificationChannel.edit_version) == base.edit_version
        )
    with session.no_autoflush:
        claimed = session.execute(
            statement.values(edit_version=col(NotificationChannel.edit_version) + 1)
            .returning(col(NotificationChannel.id))
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        if claimed is None:
            raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
        session.refresh(channel)
        if base is not None and channel_base(channel).edit_epoch != base.edit_epoch:
            raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
        _require_authority(session, actor)


def read_settings(session: Session) -> tuple[EditingBase, bool]:
    config, epoch = session.exec(
        select(SystemConfig, LibraryRevision.epoch)
        .select_from(LibraryRevision)
        .outerjoin(SystemConfig, col(SystemConfig.id) == 1)
        .where(col(LibraryRevision.id) == 1)
        .execution_options(populate_existing=True)
    ).one()
    return EditingBase(
        edit_epoch=epoch,
        edit_version=1 if config is None else config.notification_edit_version,
    ), False if config is None else config.notifications_enabled


def claim_settings(
    session: Session, actor: User, config: SystemConfig, precondition: EditPrecondition
) -> None:
    if config.id != 1:
        raise ValueError("notification_edit_requires_persisted_singleton")
    base = expected_base("notification-settings", precondition)
    statement = update(SystemConfig).where(col(SystemConfig.id) == 1)
    if base is not None:
        statement = statement.where(
            col(SystemConfig.notification_edit_version) == base.edit_version,
            select(col(LibraryRevision.epoch))
            .where(col(LibraryRevision.id) == 1)
            .scalar_subquery()
            == base.edit_epoch,
        )
    with session.no_autoflush:
        claimed = session.execute(
            statement.values(
                notification_edit_version=col(SystemConfig.notification_edit_version)
                + 1
            )
            .returning(col(SystemConfig.id))
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        if claimed is None:
            raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
        _require_authority(session, actor)
        session.refresh(config)
