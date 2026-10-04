"""Bounded sidebar reads, independent of the grid's rich Model projections."""

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.models import CollectionNodeRead, ModelFilters, OutlinerModelRead


class OutlinerView(str, Enum):
    ALL = "all"
    ORGANIZED = "organized"
    MULTIPART = "multipart"
    COMPONENTS = "components"


class OutlinerKind(str, Enum):
    MODEL = "model"
    MULTIPART = "multipart"
    COLLECTION = "collection"


class OutlinerQuery(ModelFilters):
    view: OutlinerView = OutlinerView.ALL
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=4096)
    parent_id: int | None = Field(default=None, gt=0)
    collection_id: int | None = Field(default=None, gt=0)
    reveal_id: int | None = Field(default=None, gt=0)

    def filters(self) -> ModelFilters:
        return ModelFilters.model_validate(
            self.model_dump(include=set(ModelFilters.model_fields), exclude={"q"})
        )


class OutlinerModel(OutlinerModelRead):
    kind: Literal[OutlinerKind.MODEL] = OutlinerKind.MODEL


class OutlinerMultipart(OutlinerModelRead):
    kind: Literal[OutlinerKind.MULTIPART] = OutlinerKind.MULTIPART


class OutlinerCollectionMatch(OutlinerModelRead):
    kind: Literal[OutlinerKind.COLLECTION] = OutlinerKind.COLLECTION


OutlinerEntry = Annotated[
    OutlinerModel | OutlinerMultipart, Field(discriminator="kind")
]
OutlinerMatch = Annotated[
    OutlinerModel | OutlinerMultipart | OutlinerCollectionMatch,
    Field(discriminator="kind"),
]


class OutlinerEntryPage(BaseModel):
    items: list[OutlinerEntry]
    next_cursor: str | None


class OutlinerSearchPage(BaseModel):
    items: list[OutlinerMatch]
    next_cursor: str | None


class OutlinerCollection(CollectionNodeRead):
    direct_entry_count: int
    subtree_entry_count: int
    visible_child_count: int


class OutlinerCollectionPage(BaseModel):
    items: list[OutlinerCollection]
    next_cursor: str | None
    parent_direct_entry_count: int
    revealed: OutlinerCollection | None
