"""Administrator control over how many native mesh jobs run at once.

The stored value overlays ``max_render_jobs`` like every database setting
(ADR-0002); clearing it returns to ``VAULT_MAX_RENDER_JOBS`` or, without one,
the automatic CPU-and-memory default. This process applies a change at once:
admission reads the setting per claim and the ``derive.native`` lane is
re-resolved. Separate worker processes read it when they start.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from app.core.config import ProcessRole, _overlay, settings
from app.core.errors import ErrorKind, OperationError
from app.core.host_cpus import (
    default_native_jobs,
    fitting_native_jobs,
    native_pool_bytes,
)
from app.db.models import LaneName, LibraryRevision, SystemConfig, User
from app.db.transactions import rollback_on_failure
from app.modules.administration import config_edits
from app.modules.derivatives import policy as derivative_policy
from app.schemas.editing import EditingBase

_KEY = "max_render_jobs"


class MeshWorkersSource(StrEnum):
    DATABASE = "database"
    ENVIRONMENT = "environment"
    AUTOMATIC = "automatic"


class MeshWorkersUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jobs: int = Field(strict=True, ge=1)


class MeshWorkersRead(EditingBase):
    jobs: int
    configured: int | None
    automatic: int
    maximum: int
    source: MeshWorkersSource
    restart_required: bool


def apply_mesh_workers_overlay(config: SystemConfig) -> None:
    if config.max_render_jobs is not None:
        _overlay[_KEY] = config.max_render_jobs


def maximum_jobs() -> int:
    """Most jobs whose thumbnail floor fits this process's native pool."""
    return fitting_native_jobs(native_pool_bytes(settings.mesh_memory_budget_fraction))


def read_settings(session: Session) -> MeshWorkersRead:
    config, epoch = session.exec(
        select(SystemConfig, LibraryRevision.epoch)
        .select_from(LibraryRevision)
        .outerjoin(SystemConfig, col(SystemConfig.id) == 1)
        .where(col(LibraryRevision.id) == 1)
        .execution_options(populate_existing=True)
    ).one()
    configured = None if config is None else config.max_render_jobs
    if configured is not None:
        source = MeshWorkersSource.DATABASE
    elif _KEY in settings.frozen.model_fields_set:
        source = MeshWorkersSource.ENVIRONMENT
    else:
        source = MeshWorkersSource.AUTOMATIC
    return MeshWorkersRead(
        edit_epoch=epoch,
        edit_version=1 if config is None else config.vault_edit_version,
        jobs=settings.max_render_jobs,
        configured=configured,
        automatic=default_native_jobs(settings.mesh_memory_budget_fraction),
        maximum=maximum_jobs(),
        source=source,
        # A worker process reads the vault configuration only when it starts.
        restart_required=settings.process_role is not ProcessRole.ALL,
    )


def update_settings(
    session: Session,
    update: MeshWorkersUpdate | None,
    *,
    actor: User,
    base: EditingBase | None,
) -> MeshWorkersRead:
    with rollback_on_failure(session):
        config = derivative_policy.lock(session)
        config_edits.claim(session, actor, config, base)
        if update is not None and update.jobs > maximum_jobs():
            raise OperationError("mesh_workers_exceed_memory", kind=ErrorKind.INVALID)
        config.max_render_jobs = None if update is None else update.jobs
        session.add(config)
        session.flush()
        session.commit()
        # Publish the latest committed value, even if this publisher was delayed.
        current = derivative_policy.lock(session)
        try:
            _overlay.pop(_KEY, None)
            apply_mesh_workers_overlay(current)
        finally:
            session.rollback()
        from app.modules.work import service as work_service

        work_service.reapply_lane_default(LaneName.DERIVE_NATIVE)
        return read_settings(session)
