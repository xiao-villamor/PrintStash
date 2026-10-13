"""Atomic editing claims for the Search settings aggregate.

The caller owns the complete operation's commit/rollback. A claim is not a save,
and no process-local runtime settings are published by this database operation.
"""

import re

from sqlalchemy import update
from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import LibraryRevision, SystemConfig, User
from app.schemas.editing import EditContract, EditingBase, EditPrecondition
from app.schemas.inference import SearchSettings

_MAX_EDIT_VERSION = str(2**63 - 1)


def claim(
    session: Session,
    actor: User | None,
    config: SystemConfig,
    base: EditingBase | None,
) -> None:
    """Compare and advance once; an absent base is explicit legacy compatibility."""
    if config.id != 1:
        raise ValueError("search_edit_requires_persisted_singleton")
    statement = update(SystemConfig).where(col(SystemConfig.id) == 1)
    if base is not None:
        statement = statement.where(
            col(SystemConfig.search_edit_version) == base.edit_version,
            select(col(LibraryRevision.epoch))
            .where(col(LibraryRevision.id) == 1)
            .scalar_subquery()
            == base.edit_epoch,
        )
    with session.no_autoflush:
        claimed = session.execute(
            statement.values(
                search_edit_version=col(SystemConfig.search_edit_version) + 1
            )
            .returning(col(SystemConfig.search_edit_version))
            .execution_options(synchronize_session=False)
        ).scalar_one_or_none()
        if claimed is None:
            raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
        if actor is not None:
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
                raise OperationError(
                    "config_permission_denied", kind=ErrorKind.FORBIDDEN
                )
        # Discard the caller's stale ORM snapshot before it applies the draft.
        session.refresh(config)


def etag(base: EditingBase) -> str:
    return f'"search-settings-e{base.edit_epoch}-v{base.edit_version}"'


def expected_base(precondition: EditPrecondition) -> EditingBase | None:
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
    matched = re.fullmatch(
        r'"search-settings-e([0-9a-f]{32})-v([1-9][0-9]*)"', precondition.if_match
    )
    if (
        matched is None
        or len(matched[2]) > len(_MAX_EDIT_VERSION)
        or (
            len(matched[2]) == len(_MAX_EDIT_VERSION) and matched[2] > _MAX_EDIT_VERSION
        )
    ):
        raise OperationError("edit_conflict", kind=ErrorKind.PRECONDITION_FAILED)
    return EditingBase(edit_epoch=matched[1], edit_version=int(matched[2]))


def read(session: Session) -> tuple[EditingBase, SearchSettings]:
    """Read settings and database history from the same statement."""
    config, epoch = session.exec(
        select(SystemConfig, LibraryRevision.epoch)
        .select_from(LibraryRevision)
        .outerjoin(SystemConfig, col(SystemConfig.id) == 1)
        .where(col(LibraryRevision.id) == 1)
        .execution_options(populate_existing=True)
    ).one()
    return (
        EditingBase(
            edit_epoch=epoch,
            edit_version=1 if config is None else config.search_edit_version,
        ),
        SearchSettings.model_validate_json(config.ai_search_settings_json)
        if config is not None and config.ai_search_settings_json is not None
        else SearchSettings(),
    )
