"""Revision-checked heterogeneous library card contract."""

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.models import ModelFilters, ModelListItem, ModelSort
from app.schemas.multipart_models import MultipartModelListItem


class LibraryView(str, Enum):
    ALL = "all"
    MULTIPART = "multipart"


class BrowseKind(str, Enum):
    MODEL = "model"
    MULTIPART = "multipart"


class BrowseQuery(ModelFilters):
    view: LibraryView = LibraryView.ALL
    sort: ModelSort = ModelSort.DATE_DESC
    limit: int = Field(default=60, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=4096)

    def filters(self) -> ModelFilters:
        return ModelFilters.model_validate(
            self.model_dump(include=set(ModelFilters.model_fields))
        )


class BrowseModel(BaseModel):
    kind: Literal[BrowseKind.MODEL] = BrowseKind.MODEL
    model: ModelListItem


class BrowseMultipart(BaseModel):
    kind: Literal[BrowseKind.MULTIPART] = BrowseKind.MULTIPART
    multipart: MultipartModelListItem


BrowseEntry = Annotated[BrowseModel | BrowseMultipart, Field(discriminator="kind")]


class BrowsePage(BaseModel):
    items: list[BrowseEntry]
    next_cursor: str | None
    total: int = Field(
        ge=0, description="Combined Model and Multipart Model card count."
    )
    browse_revision: str
    authorization_revision: str


class BrowseRevisionRead(BaseModel):
    browse_revision: str
    authorization_revision: str


class BrowseThumbnailQuery(BaseModel):
    model_id: list[Annotated[int, Field(gt=0)]] = Field(min_length=1, max_length=24)


class BrowseThumbnail(BaseModel):
    model_id: int
    thumbnail_url: str | None


class BrowseThumbnailsRead(BaseModel):
    items: list[BrowseThumbnail]
    authorization_revision: str
