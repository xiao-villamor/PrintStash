"""Pure state-machine policy for durable Artifact upload sessions."""

from app.db.models.types import ArtifactUploadState


class ArtifactUploadTransitionError(ValueError):
    """A caller attempted a transition the upload protocol cannot produce."""


_TRANSITIONS: dict[ArtifactUploadState, frozenset[ArtifactUploadState]] = {
    ArtifactUploadState.CREATED: frozenset(
        {
            ArtifactUploadState.UPLOADING,
            ArtifactUploadState.FAILED,
            ArtifactUploadState.ABORTED,
            ArtifactUploadState.EXPIRED,
        }
    ),
    ArtifactUploadState.UPLOADING: frozenset(
        {
            ArtifactUploadState.VERIFYING,
            ArtifactUploadState.FAILED,
            ArtifactUploadState.ABORTED,
            ArtifactUploadState.EXPIRED,
        }
    ),
    ArtifactUploadState.VERIFYING: frozenset(
        {
            ArtifactUploadState.INGESTING,
            ArtifactUploadState.FAILED,
            ArtifactUploadState.ABORTED,
        }
    ),
    ArtifactUploadState.INGESTING: frozenset(
        {ArtifactUploadState.COMPLETED, ArtifactUploadState.FAILED}
    ),
    ArtifactUploadState.FAILED: frozenset(
        {
            ArtifactUploadState.UPLOADING,
            ArtifactUploadState.ABORTED,
            ArtifactUploadState.EXPIRED,
        }
    ),
    ArtifactUploadState.COMPLETED: frozenset(),
    ArtifactUploadState.ABORTED: frozenset(),
    ArtifactUploadState.EXPIRED: frozenset(),
}


def require_transition(
    current: ArtifactUploadState,
    target: ArtifactUploadState,
    *,
    retryable: bool = False,
) -> None:
    """Accept idempotency and legal edges; reject resurrection of terminal rows."""

    if current is target:
        return
    if (
        current is ArtifactUploadState.FAILED
        and target is ArtifactUploadState.UPLOADING
        and not retryable
    ):
        raise ArtifactUploadTransitionError("artifact_upload_not_retryable")
    if target not in _TRANSITIONS[current]:
        raise ArtifactUploadTransitionError(
            f"artifact_upload_transition_invalid:{current.value}:{target.value}"
        )
