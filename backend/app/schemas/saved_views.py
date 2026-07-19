from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.models import ModelFilters


class SavedViewFilters(ModelFilters):
    pass


class SavedViewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    filters: SavedViewFilters


class SavedViewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    filters: Optional[SavedViewFilters] = None


class SavedViewRead(BaseModel):
    id: int
    name: str
    filters: SavedViewFilters
    created_at: datetime
    updated_at: datetime


class ModelStarRead(BaseModel):
    model_id: int
    starred: bool
