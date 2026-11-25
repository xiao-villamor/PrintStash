"""First ownership of an installation.

An installation is claimed exactly once, either from the browser (the account
and its storage in one request) or, with ``VAULT_SETUP_MODE=environment``, from
``VAULT_SETUP_ADMIN_*`` at startup (the account only; the owner signs in and
chooses storage afterwards). ``setup_policy`` decides which door exists. Both paths
serialize on the database so competing API processes cannot create two first
administrators.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlmodel import Session, select

from app.core.config import settings
from app.core.errors import ErrorKind, OperationError
from app.core.logging import get_logger
from app.db.models import SystemConfig, User
from app.modules.administration import runtime_config, setup_policy, setup_storage
from app.modules.identity.auth import hash_password
from app.schemas.setup import SetupPreviewMode, SetupRequest

logger = get_logger(__name__)


@dataclass(frozen=True)
class Ownership:
    user: User
    storage_ready: bool


def require_open(session: Session) -> None:
    config = session.get(SystemConfig, 1)
    if config is not None and config.configured_at is not None:
        raise OperationError(kind=ErrorKind.CONFLICT, detail="already_configured")
    if session.exec(select(User.id).limit(1)).first() is not None:
        raise OperationError(kind=ErrorKind.CONFLICT, detail="users_already_exist")


def lock_installation(session: Session) -> None:
    """Serialize first ownership in the database, not in one API process."""
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    elif connection.dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(72816409531)"))
    else:
        raise OperationError(
            kind=ErrorKind.UNAVAILABLE, detail="setup_database_not_supported"
        )
    session.expire_all()
    require_open(session)


def _stage_owner(session: Session, request: SetupRequest) -> tuple[User, SystemConfig]:
    """Stage the superuser and the configured stamp; the caller commits."""
    user = User(
        username=request.username.strip(),
        email=(request.email.strip() if request.email else None) or None,
        hashed_password=hash_password(request.password),
        is_superuser=True,
        is_active=True,
    )
    session.add(user)
    config = runtime_config.mark_configured(session, commit=False)
    # Storage stays pending until it is activated: a failed activation keeps
    # the account and is retried by the authenticated owner.
    config.setup_storage_pending = True
    session.add(config)
    return user, config


def claim(session: Session, request: SetupRequest) -> Ownership:
    """Create the browser-registered owner together with the chosen storage.

    The caller holds :func:`lock_installation`.
    """
    prepared = setup_storage.prepare(request, session, provision=True)
    setup_storage.persist_choice(session, request, prepared)
    user, config = _stage_owner(session, request)
    if request.preview_appearance is not None:
        config.preview_look = request.preview_appearance.preview_look
        config.preview_floor_grid = request.preview_appearance.preview_floor_grid
    if request.preview_mode is not None:
        config.derivatives_previews_enabled = (
            request.preview_mode is not SetupPreviewMode.SINGLE
        )
        config.derivatives_previews_include_poles = (
            request.preview_mode is SetupPreviewMode.OBLIQUE_POLES
        )
    session.commit()
    session.refresh(user)
    # Runtime activation is part of the recoverable preparation below.
    storage_ready = True
    try:
        setup_storage.finish(session, config)
    except Exception:
        session.rollback()
        storage_ready = False
        logger.warning("first-run account created; storage preparation needs retry")

    logger.info(
        "first-run setup complete: user=%s data_dir=%s thumb_dir=%s",
        user.username,
        settings.data_dir,
        settings.thumb_dir,
    )
    return Ownership(user=user, storage_ready=storage_ready)


def provision_from_environment(session: Session) -> User | None:
    """Create the first administrator from ``VAULT_SETUP_ADMIN_*``, once.

    Acts only on ``VAULT_SETUP_MODE=environment``. Only an installation without an
    owner is provisioned; an existing account is never changed, so a variable left
    in place cannot reset a password on the next restart. Storage is not chosen
    here: the administrator signs in and chooses it in the browser.
    """
    policy = setup_policy.current()
    if isinstance(policy, setup_policy.Misconfigured):
        logger.error("first-run setup is misconfigured: %s", policy.describe())
        return None
    if not isinstance(policy, setup_policy.Environment):
        return None
    try:
        lock_installation(session)
    except OperationError:
        session.rollback()
        logger.debug("installation already has an owner; VAULT_SETUP_ADMIN_* ignored")
        return None
    user, _config = _stage_owner(session, policy.request)
    session.commit()
    session.refresh(user)
    logger.info(
        "first administrator %s provisioned from VAULT_SETUP_ADMIN_USERNAME; "
        "sign in to choose storage",
        user.username,
    )
    return user
