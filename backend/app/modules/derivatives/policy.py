"""Deployment defaults and durable, live controls for derivative producers."""

from dataclasses import dataclass
from enum import StrEnum

from sqlmodel import Session, select

from app.core.config import settings
from app.core.errors import ErrorKind, OperationError
from app.core.time import utcnow
from app.db.library_revision import invalidate_library_browse
from app.db.models import (
    DerivativeGroupRegeneration,
    DerivativeKind,
    JobKind,
    SystemConfig,
)
from app.db.transactions import begin_write
from app.modules.work.contracts import SkipReason


class SettingName(StrEnum):
    MESH = "derivatives_mesh_enabled"
    GCODE = "derivatives_gcode_enabled"
    TOOLPATH = "derivatives_toolpath_enabled"
    # Independent of MESH so operators can keep covers while skipping the
    # extra CPU and storage of generated views. Views still need a cover.
    PREVIEWS = "derivatives_previews_enabled"
    PREVIEW_POLES = "derivatives_previews_include_poles"


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


def _group_policy(config: SystemConfig | None, definition: JobKind) -> GroupPolicy:
    name = SETTINGS[definition]
    default = getattr(settings.frozen, name)
    override = getattr(config, name) if config is not None else None
    return GroupPolicy(
        enabled=default if override is None else override,
        default_enabled=default,
        overridden=override is not None,
    )


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
    controls = {
        definition: _group_policy(config, definition) for definition in SETTINGS
    }
    session.info[_RESOLVED] = (_scope(session), controls)
    return dict(controls)


def preview_poles(session: Session) -> bool:
    override = session.exec(
        select(SystemConfig.derivatives_previews_include_poles).where(
            SystemConfig.id == 1
        )
    ).first()
    return (
        settings.frozen.derivatives_previews_include_poles
        if override is None
        else override
    )


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


def lock_generation(session: Session) -> None:
    """Share generation authority among publishers; regeneration takes it exclusively."""
    begin_write(session, immediate=True)
    config_id = session.exec(
        select(SystemConfig.id).where(SystemConfig.id == 1).with_for_update(read=True)
    ).first()
    if config_id is None:
        # Bootstrap normally creates it. The exceptional first writer must
        # actually establish authority, never publish without a locked row.
        lock(session)


def admission(session: Session, definition: JobKind) -> SkipReason | None:
    return (
        None
        if resolve(session)[definition].enabled
        else SkipReason.DERIVATIVE_GROUP_DISABLED
    )


def _require_enabled(control: GroupPolicy) -> None:
    if not control.enabled:
        raise OperationError(
            SkipReason.DERIVATIVE_GROUP_DISABLED, kind=ErrorKind.CONFLICT
        )


def require_enabled(session: Session, definition: JobKind) -> None:
    _require_enabled(resolve(session)[definition])


def lock_enabled(session: Session, definition: JobKind) -> None:
    """Check admission from the row already locked for the producer's claim."""
    _require_enabled(_group_policy(lock(session), definition))


def update(
    session: Session, changes: dict[SettingName, bool | None], *, commit: bool = True
) -> None:
    """Persist all supplied controls together, then publish latency hints."""
    if not changes:
        return
    config = lock(session)
    previous_poles = preview_poles(session)
    previous_enabled = resolve(session)[JobKind.DERIVATIVES_PREVIEWS].enabled
    for name, value in changes.items():
        setattr(config, name, value)
    _forget(session)
    config.updated_at = utcnow()
    session.add(config)
    session.flush()
    poles_changed = previous_poles != preview_poles(session)
    if (
        poles_changed
        or previous_enabled != resolve(session)[JobKind.DERIVATIVES_PREVIEWS].enabled
    ):
        invalidate_library_browse(session)
    if poles_changed:
        # Fence in-flight publication and rediscover existing outputs atomically
        # with the setting, using the same durable authority as regenerate-all.
        definition, kind = JobKind.DERIVATIVES_PREVIEWS, DerivativeKind.PREVIEWS
        request = session.get(DerivativeGroupRegeneration, (definition, kind))
        if request is None:
            request = DerivativeGroupRegeneration(definition=definition, kind=kind)
        request.requested_at = config.updated_at
        session.add(request)
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
        if name in changes or (
            definition == JobKind.DERIVATIVES_PREVIEWS
            and SettingName.PREVIEW_POLES in changes
        ):
            nudge(definition)
