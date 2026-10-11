"""Failures shared by disposable mesh supervisors and their continuations."""

from app.core.cancellation import OperationCancelled
from app.modules.media.mesh_contracts import ThumbnailFailureReason
from app.modules.media.mesh_telemetry import SupervisionStats


class MeshWorkerError(Exception):
    """The child could not produce a result; `reason` says why."""

    def __init__(self, reason: ThumbnailFailureReason) -> None:
        super().__init__(reason.value)
        self.reason = reason
        self.supervision: SupervisionStats | None = None


class MeshWorkerCancelled(OperationCancelled):
    """Withdrawal preserves observed parent costs without becoming job failure."""

    def __init__(self, supervision: SupervisionStats) -> None:
        super().__init__()
        self.supervision = supervision
