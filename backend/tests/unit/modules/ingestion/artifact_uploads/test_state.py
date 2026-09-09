import pytest

from app.db.models import ArtifactUploadState
from app.modules.ingestion.artifact_uploads import (
    ArtifactUploadTransitionError,
    require_transition,
)


@pytest.mark.parametrize("state", list(ArtifactUploadState))
def test_every_state_is_idempotent(state: ArtifactUploadState) -> None:
    require_transition(state, state)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ArtifactUploadState.CREATED, ArtifactUploadState.UPLOADING),
        (ArtifactUploadState.UPLOADING, ArtifactUploadState.VERIFYING),
        (ArtifactUploadState.VERIFYING, ArtifactUploadState.INGESTING),
        (ArtifactUploadState.INGESTING, ArtifactUploadState.COMPLETED),
        (ArtifactUploadState.CREATED, ArtifactUploadState.ABORTED),
        (ArtifactUploadState.UPLOADING, ArtifactUploadState.EXPIRED),
    ],
)
def test_accepts_legal_transition(
    current: ArtifactUploadState, target: ArtifactUploadState
) -> None:
    require_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (ArtifactUploadState.CREATED, ArtifactUploadState.COMPLETED),
        (ArtifactUploadState.VERIFYING, ArtifactUploadState.CREATED),
        (ArtifactUploadState.COMPLETED, ArtifactUploadState.UPLOADING),
        (ArtifactUploadState.ABORTED, ArtifactUploadState.CREATED),
        (ArtifactUploadState.EXPIRED, ArtifactUploadState.UPLOADING),
    ],
)
def test_rejects_illegal_transition(
    current: ArtifactUploadState, target: ArtifactUploadState
) -> None:
    with pytest.raises(ArtifactUploadTransitionError, match="transition_invalid"):
        require_transition(current, target)


def test_failed_session_resumes_only_when_error_is_retryable() -> None:
    with pytest.raises(ArtifactUploadTransitionError, match="not_retryable"):
        require_transition(ArtifactUploadState.FAILED, ArtifactUploadState.UPLOADING)

    require_transition(
        ArtifactUploadState.FAILED,
        ArtifactUploadState.UPLOADING,
        retryable=True,
    )
