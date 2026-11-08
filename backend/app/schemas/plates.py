"""Public shapes of a slicer project 3MF's plates."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, model_validator

from app.schemas.jobs import DerivativeStatus


class PlateRegionRead(BaseModel):
    """A plate's rectangle of the build, in the package's millimetres."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float


class FilePlateRead(BaseModel):
    number: int
    # The slicer leaves a plate unnamed until the user names it.
    name: Optional[str]
    object_count: int
    # Only a plate whose slicer saved a preview has one.
    image_url: Optional[str]
    region: PlateRegionRead


class FilePlatesRead(BaseModel):
    """The plates derivative of one 3MF Artifact.

    ``plates`` is the layout once the derivative is ``ready``; in every other
    state the layout is unknown (``pending``, ``failed``…) or absent
    (``skipped``: the package is not a slicer project) and the list is empty.
    """

    state: DerivativeStatus
    plates: list[FilePlateRead]

    @model_validator(mode="after")
    def plates_only_when_ready(self) -> FilePlatesRead:
        if (self.state is DerivativeStatus.READY) != bool(self.plates):
            raise ValueError("plates_require_a_ready_layout")
        return self


class GcodePlatesRead(BaseModel):
    """Numbers of the sliced plates actually present in a 3MF archive."""

    plates: list[int]
