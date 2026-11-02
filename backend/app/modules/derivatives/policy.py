"""Deployment defaults and durable, live controls for derivative producers."""

from dataclasses import dataclass
from enum import StrEnum

from sqlmodel import Session, select

from app.core.config import settings
from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.models import JobKind, SystemConfig
from app.db.transactions import begin_write
from app.modules.work.contracts import SkipReason


class SettingName(StrEnum):
    MESH = "derivatives_mesh_enabled"
    GCODE = "derivatives_gcode_enabled"
    TOOLPATH = "derivatives_toolpath_enabled"
    # Independent of MESH so operators can keep covers while skipping the
    # extra CPU and storage of generated views. Views still need a cover.
    PREVIEWS = "derivatives_previews_enabled"


SETTINGS = {
    JobKind.DERIVATIVES_MESH: SettingName.MESH,
    JobKind.DERIVATIVES_PREVIEWS: SettingName.PREVIEWS,
    JobKind.DERIVATIVES_VIEWER_STL: SettingName.MESH,
    JobKind.DERIVATIVES_PLATES: SettingName.MESH,
    JobKind.DERIVATIVES_GCODE: SettingName.GCODE,
    JobKind.DERIVATIVES_TOOLPATH: SettingName.TOOLPATH,
}


@dataclass(frozen=True)
class GroupPolicy:
    enabled: bool
    default_enabled: bool
    overridden: bool


# session.info key: the controls already read in this session's transaction.
_RESOLVED = "derivative_policy_resolved"


def _scope(session: Session) -> object | None:
    """The transaction (or savepoint) whose snapshot a read belongs to."""
    return session.get_nested_transaction() or session.get_transaction()


def _forget(session: Session) -> None:
    session.info.pop(_RESOLVED, None)


def resolve(session: Session) -> dict[JobKind, GroupPolicy]:
    """One fresh database read per transaction; process overlays never count.

    A claim, a discovery pass and a publication each ask several times inside
    one transaction, whose snapshot cannot change under them; every write to
    the controls goes through ``lock``, which forgets the read.
    """
    scope = _scope(session)
    cached = session.info.get(_RESOLVED)
    if scope is not None and cached is not None and cached[0] is scope:
        return dict(cached[1])
    config = session.exec(
        select(SystemConfig)
        .where(SystemConfig.id == 1)
        .execution_options(populate_existing=True)
    ).first()
    controls = {}
    for definition, name in SETTINGS.items():
        default = getattr(settings.frozen, name)
        override = getattr(config, name) if config is not None else None
        controls[definition] = GroupPolicy(
            enabled=default if override is None else override,
            default_enabled=default,
            overridden=override is not None,
        )
    session.info[_RESOLVED] = (_scope(session), controls)
    return dict(controls)


def lock(session: Session) -> SystemConfig:
    """Serialize settings edits with producer admission on both supported DBs."""
    _forget(session)
    begin_write(session, immediate=True)
    config = session.exec(
        select(SystemConfig)
        .where(SystemConfig.id == 1)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if config is None:
        # Normal startup creates this singleton before jobs launch.
        config = SystemConfig(id=1)
        session.add(config)
        session.flush()
    return config


def lock_generation(session: Session) -> SystemConfig:
    """Share generation authority among publishers; regeneration takes it exclusively."""
    begin_write(session, immediate=True)
    config = session.exec(
        select(SystemConfig).where(SystemConfig.id == 1).with_for_update(read=True)
    ).first()
    if config is None:
        # Bootstrap normally creates it. The exceptional first writer must
        # actually establish authority, never publish without a locked row.
        return lock(session)
    return config


def admission(session: Session, definition: JobKind) -> SkipReason | None:
    return (
        None
        if resolve(session)[definition].enabled
        else SkipReason.DERIVATIVE_GROUP_DISABLED
    )


def require_enabled(session: Session, definition: JobKind) -> None:
    if admission(session, definition) is not None:
        raise OperationError(
            SkipReason.DERIVATIVE_GROUP_DISABLED, kind=ErrorKind.CONFLICT
        )


def update(
    session: Session, changes: dict[SettingName, bool | None], *, commit: bool = True
) -> None:
    """Persist all supplied controls together, then publish latency hints."""
    if not changes:
        return
    config = lock(session)
    for name, value in changes.items():
        setattr(config, name, value)
    _forget(session)
    config.updated_at = utcnow()
    session.add(config)
    if commit:
        session.commit()
        publish_changes(changes)


def publish_changes(changes: dict[SettingName, bool | None]) -> None:
    """Publish producer wake-up hints only after the caller commits its controls."""
    if not changes:
        return
    from app.modules.work.events import derivative_policy_changed
    from app.modules.work.submission import nudge

    derivative_policy_changed()
    for definition, name in SETTINGS.items():
        if name in changes:
            nudge(definition)
