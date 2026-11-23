"""A frozen review choice, independent of extraction or publication side effects."""

from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.core.errors import OperationError


class ReviewedEntry(Protocol):
    name: str
    file_type: str | None
    is_image: bool
    is_document: bool


class _Intake(BaseModel):
    model_config = ConfigDict(extra="forbid")
    documents: list[str] = Field(default_factory=list, max_length=100)


class SeparateIntake(_Intake):
    mode: Literal["separate"]


class ArchivePart(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entry_name: str = Field(min_length=1)
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
    ]


class MultipartIntake(_Intake):
    mode: Literal["multipart"]
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)
    ]
    parts: list[ArchivePart] = Field(min_length=1, max_length=100)
    cover: str | None = None


ArchiveIntake = Annotated[SeparateIntake | MultipartIntake, Field(discriminator="mode")]


def validate_selection(
    plan: ArchiveIntake, entries: Sequence[ReviewedEntry], model_names: Sequence[str]
) -> None:
    """Refuse unreviewed guides or a grouping different from the selected Models."""
    if len(set(plan.documents)) != len(plan.documents):
        raise OperationError("archive_duplicate_selection")
    documents = {entry.name for entry in entries if entry.is_document or entry.is_image}
    if not set(plan.documents) <= documents:
        raise OperationError("archive_document_not_found")
    if isinstance(plan, MultipartIntake):
        if plan.cover is not None:
            images = {
                entry.name
                for entry in entries
                if entry.is_image
                and PurePosixPath(entry.name).suffix.lower()
                in {".png", ".jpg", ".jpeg", ".webp"}
            }
            if plan.cover not in images:
                raise OperationError("archive_cover_not_found")
        names = [part.entry_name for part in plan.parts]
        if len(set(names)) != len(names):
            raise OperationError("archive_duplicate_selection")
        if set(names) != set(model_names):
            raise OperationError("archive_part_selection_mismatch")
