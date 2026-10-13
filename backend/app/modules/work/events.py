"""Realtime notices for background work: Job changes and derivative changes.

A notice carries ids and a hint, never data a reader needs authorization
for: the client refetches through the authorized endpoints. Channels:

- ``jobs:<user id>``: the owner's Jobs changed;
- ``work:admin``: any Job changed (the admin Background work page);
- ``model:<model id>``: a derivative of one of the Model's Artifacts changed.

Publication goes through the process's bound publisher (in memory, or
``pg_notify`` from a worker) and is dropped when none is bound.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.core.logging import get_logger
from app.db.models import DerivativeKind, DerivativeState, JobKind
from app.schemas.jobs import JobStatus

logger = get_logger(__name__)


class Publisher(Protocol):
    def publish_threadsafe(self, channel: str, payload: dict[str, Any]) -> None: ...


_publisher: Publisher | None = None


def bind(publisher: Publisher | None) -> None:
    global _publisher
    _publisher = publisher


def _publish(channel: str, payload: dict[str, Any]) -> None:
    publisher = _publisher
    if publisher is None:
        return
    try:
        publisher.publish_threadsafe(channel, payload)
    except Exception:  # noqa: BLE001 - realtime delivery is best effort
        logger.warning("work event publication failed", extra={"channel": channel})


POLICY_CHANNEL = "derivatives:policy"


def derivative_policy_changed() -> None:
    _publish(POLICY_CHANNEL, {"type": "derivative_policy"})


def job_changed(status: JobStatus) -> None:
    notice = {
        "type": "job",
        "job_id": status.job_id,
        "kind": status.kind,
        "state": status.state,
        "progress": status.progress,
        # The admin socket also carries maintenance absent from the default
        # Tasks list. Tell that reader whether its snapshot can have changed.
        "task_visible": status.owner_user_id is not None
        or status.kind == JobKind.BACKUPS_AUTOMATIC,
    }
    if status.owner_user_id is not None:
        _publish(f"jobs:{status.owner_user_id}", notice)
    _publish("work:admin", notice)


def derivative_changed(
    *, model_id: int, file_id: int, kind: DerivativeKind, state: DerivativeState
) -> None:
    _publish(
        f"model:{model_id}",
        {
            "type": "derivative",
            "model_id": model_id,
            "file_id": file_id,
            "kind": kind,
            "state": state,
        },
    )
