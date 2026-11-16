"""A preview summary cannot claim states its fields contradict."""

import pytest
from pydantic import TypeAdapter, ValidationError

from app.schemas.previews import (
    ArtifactPreviews,
    ModelPreviewRead,
    PreviewState,
    PublishedPreviewRead,
)

PUBLISHED = PublishedPreviewRead(
    profile="orbit",
    manifest_url="/api/v1/models/1/previews/g",
    views=[
        {"id": "orbit-0", "role": "orbit", "url": "/api/v1/models/1/previews/g/orbit-0"}
    ],
)


class TestArtifactPreviews:
    @pytest.mark.parametrize(
        "state", [PreviewState.PENDING, PreviewState.QUEUED, PreviewState.READY]
    )
    def test_rejects_a_reason_outside_failure_states(self, state):
        with pytest.raises(ValidationError, match="preview_reason_without_failure"):
            ArtifactPreviews(file_id=1, state=state, reason="x", published=PUBLISHED)

    def test_rejects_ready_without_publication(self):
        with pytest.raises(ValidationError, match="ready_without_publication"):
            ArtifactPreviews(
                file_id=1, state=PreviewState.READY, reason=None, published=None
            )

    def test_keeps_failure_reasons_with_previous_publication(self):
        summary = ArtifactPreviews(
            file_id=1,
            state=PreviewState.FAILED,
            reason="resource_limit",
            published=PUBLISHED,
        )
        assert summary.published == PUBLISHED

    def test_rejects_an_empty_publication(self):
        with pytest.raises(ValidationError):
            PublishedPreviewRead(profile="orbit", manifest_url="/m", views=[])

    def test_discriminates_models_without_a_mesh(self):
        adapter = TypeAdapter(ModelPreviewRead)
        assert adapter.validate_python({"kind": "none"}).cover_camera == "unknown"
        with pytest.raises(ValidationError):
            adapter.validate_python({"kind": "unknown"})
