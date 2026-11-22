"""Additive Model presentation references; covers have no inferred camera.

A Model either has no live mesh Artifact to derive views from (``none``), or
exactly one selected mesh Artifact whose lifecycle and last valid publication
are reported together (``artifact``). Published views are present only when a
valid generation exists, so a pending replacement never hides prior images.
"""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.media.preview_contracts import (
    MAX_VIEWS,
    PreviewImageKind,
    PreviewManifest,
    ViewId,
    ViewProfile,
    ViewRole,
)


class PreviewState(StrEnum):
    DISABLED = "disabled"
    # No derivative row exists yet for the current recipe.
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    READY = "ready"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNSUPPORTED = "unsupported"


# Lifecycles whose stored failure reason explains why no current set exists.
REASONED_STATES = frozenset(
    {PreviewState.FAILED, PreviewState.CANCELLED, PreviewState.UNSUPPORTED}
)


class PreviewImageRead(BaseModel):
    image_kind: Literal[PreviewImageKind.RENDER] = PreviewImageKind.RENDER
    id: ViewId
    role: ViewRole
    url: str
    thumbnail_url: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class PublishedPreviewRead(BaseModel):
    profile: ViewProfile
    manifest_url: str
    # Only present when the displayed cover and a published view have identical bytes.
    cover_view: ViewId | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    views: list[PreviewImageRead] = Field(min_length=1, max_length=MAX_VIEWS)


class ArchiveImageRead(BaseModel):
    image_kind: Literal[PreviewImageKind.PHOTO, PreviewImageKind.SLICER_RENDER]
    file_id: int
    url: str
    plate: int | None


class SourceImageRead(BaseModel):
    id: str
    role: Literal["source"] = "source"
    source_id: int
    position: int
    provider: str
    url: str


class NoPreviewSubject(BaseModel):
    """The Model has no live mesh Artifact; only its cover can be shown."""

    # Always serialized, so clients see the discriminator as required.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    kind: Literal["none"] = "none"
    # Embedded, source and custom covers carry no known mesh camera.
    cover_camera: Literal["unknown"] = "unknown"
    source_images: list[SourceImageRead] = Field(default_factory=list)
    cover_image_kind: PreviewImageKind | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    large_cover_image_kind: PreviewImageKind | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    large_cover_url: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    large_cover_view: ViewId | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    archive_images: list[ArchiveImageRead] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )


class ArtifactPreviews(BaseModel):
    """The view set of the one mesh Artifact selected for this Model."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    kind: Literal["artifact"] = "artifact"
    cover_camera: Literal["unknown"] = "unknown"
    source_images: list[SourceImageRead] = Field(default_factory=list)
    cover_image_kind: PreviewImageKind | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    large_cover_image_kind: PreviewImageKind | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    large_cover_url: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    large_cover_view: ViewId | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    archive_images: list[ArchiveImageRead] = Field(
        default_factory=list, exclude_if=lambda value: not value
    )
    large_cover_thumbnail_url: str | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    file_id: int
    state: PreviewState
    reason: str | None
    published: PublishedPreviewRead | None

    @model_validator(mode="after")
    def reason_matches_state(self) -> "ArtifactPreviews":
        if self.reason is not None and self.state not in REASONED_STATES:
            raise ValueError("preview_reason_without_failure")
        if self.state is PreviewState.READY and self.published is None:
            raise ValueError("preview_ready_without_publication")
        return self


ModelPreviewRead = Annotated[
    NoPreviewSubject | ArtifactPreviews, Field(discriminator="kind")
]


class PreviewManifestRead(PreviewManifest):
    file_id: int
    generation: str
