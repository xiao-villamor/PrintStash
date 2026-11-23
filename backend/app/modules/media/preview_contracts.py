"""Versioned, bounded wire format for a published multi-angle derivative."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Older manifests remain readable at their published size.
MIN_WIDTH = 320
MAX_WIDTH = 1280
GALLERY_WIDTH = MAX_WIDTH
STRIP_WIDTH = MIN_WIDTH
MAX_VIEWS = 7


def height_for(width: int) -> int:
    """The 4:3 height every cover and view of ``width`` has."""
    return round(width * 3 / 4)


# Source size and face count follow the shared mesh limits
# (VAULT_MESH_MAX_LOAD_MB, VAULT_MESH_MAX_RENDER_TRIANGLES); see preview_worker.
MAX_IMAGE_BYTES = 1024 * 1024
MAX_STRIP_BYTES = 128 * 1024
MAX_BUNDLE_BYTES = MAX_VIEWS * (MAX_IMAGE_BYTES + MAX_STRIP_BYTES)
MAX_MANIFEST_BYTES = 32 * 1024

Vector = tuple[float, float, float]
Matrix = tuple[Vector, Vector, Vector]


class PreviewLimitError(Exception):
    """A documented generation limit was exceeded; the Artifact stays valid."""


class PreviewImageKind(StrEnum):
    PHOTO = "photo"
    RENDER = "render"
    SLICER_RENDER = "slicer_render"


class ViewId(StrEnum):
    ORBIT_0 = "orbit-0"
    ORBIT_1 = "orbit-1"
    ORBIT_2 = "orbit-2"
    ORBIT_3 = "orbit-3"
    TOP = "top"
    BOTTOM = "bottom"
    FACE = "face"
    REVERSE = "reverse"
    EDGE = "edge"


class PreviewSize(StrEnum):
    FULL = "full"
    THUMBNAIL = "thumbnail"


class ViewRole(StrEnum):
    ORBIT = "orbit"
    TOP = "top"
    BOTTOM = "bottom"
    FACE = "face"
    REVERSE = "reverse"
    EDGE = "edge"


class ViewProfile(StrEnum):
    ORBIT = "orbit"
    PLANAR = "planar"


class CameraView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    id: ViewId
    role: ViewRole
    rotation: Matrix
    azimuth_degrees: float | None
    elevation_degrees: float | None


class PreviewSpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    offset: int = Field(ge=0, lt=MAX_BUNDLE_BYTES)
    size: int = Field(gt=0, le=MAX_STRIP_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PublishedView(CameraView):
    width: int = Field(ge=MIN_WIDTH, le=MAX_WIDTH)
    height: int
    projection: Literal["orthographic"] = "orthographic"
    # x/y midpoints in centered view space, before pixel-axis inversion.
    view_center: tuple[float, float]
    pixels_per_unit: float = Field(gt=0)
    offset: int = Field(ge=0, lt=MAX_BUNDLE_BYTES)
    size: int = Field(gt=0, le=MAX_IMAGE_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    thumbnail: PreviewSpan | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def four_by_three(self) -> "PublishedView":
        if self.height != height_for(self.width):
            raise ValueError("invalid_preview_dimensions")
        return self


class PreviewManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    version: Literal[1] = 1
    recipe: int = Field(default=1, ge=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile: ViewProfile
    appearance: str | None = Field(default=None, exclude_if=lambda value: value is None)
    center: Vector
    views: tuple[PublishedView, ...] = Field(min_length=1, max_length=MAX_VIEWS)
    cover_view: ViewId | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    size: int = Field(gt=0, le=MAX_BUNDLE_BYTES)

    @model_validator(mode="after")
    def contiguous_views(self) -> "PreviewManifest":
        position = 0
        ids = set()
        for view in self.views:
            if view.id in ids or view.offset != position:
                raise ValueError("invalid_preview_layout")
            ids.add(view.id)
            position += view.size
            if view.thumbnail is not None:
                if view.thumbnail.offset != position:
                    raise ValueError("invalid_preview_thumbnail_layout")
                position += view.thumbnail.size
        if self.cover_view is not None and self.cover_view not in ids:
            raise ValueError("invalid_preview_cover")
        if position != self.size:
            raise ValueError("invalid_preview_size")
        # One set is one rendering: every view shares the setting it used.
        if len({(view.width, view.height) for view in self.views}) != 1:
            raise ValueError("invalid_preview_dimensions")
        return self
