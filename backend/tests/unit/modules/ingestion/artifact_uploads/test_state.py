"""Defend the upload state machine's legal and retryable transitions.

A failure here means concurrent or resumed uploads could cross a terminal boundary.
"""

import pytest

from app.db.models import ArtifactUploadState
from app.modules.ingestion.artifact_uploads import (
    ArtifactUploadTransitionError,
    require_transition,
)


class TestRequireTransition:
    @pytest.mark.parametrize("state", list(ArtifactUploadState))
    def test_accepts_idempotency(self, state: ArtifactUploadState) -> None:
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
        self, current: ArtifactUploadState, target: ArtifactUploadState
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
        self, current: ArtifactUploadState, target: ArtifactUploadState
    ) -> None:
        with pytest.raises(ArtifactUploadTransitionError, match="transition_invalid"):
            require_transition(current, target)

    def test_requires_retryable_failure_for_resume(self) -> None:
        with pytest.raises(ArtifactUploadTransitionError, match="not_retryable"):
            require_transition(
                ArtifactUploadState.FAILED, ArtifactUploadState.UPLOADING
            )

        require_transition(
            ArtifactUploadState.FAILED,
            ArtifactUploadState.UPLOADING,
            retryable=True,
        )
