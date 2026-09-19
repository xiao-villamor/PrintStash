"""Only canonical, locally validated filters leave the parsing boundary."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.core.timezones import timezone_name
from app.schemas.models import ModelFilters

SearchSort = Literal[
    "relevance", "date-desc", "date-asc", "name-asc", "name-desc", "printed-desc"
]


class SearchPreferencesPatch(BaseModel):
    model_config = {"extra": "forbid"}
    nl_filters_enabled: bool | None = None
    timezone: str | None = Field(default=None, max_length=128)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value):
        return timezone_name(value) if value is not None else None


class SearchPreferencesRead(BaseModel):
    nl_filters_enabled: bool = False
    timezone: str | None = None
    effective_timezone: str
    available: bool
    endpoint_host: str | None = None


class ParseSearchRequest(BaseModel):
    model_config = {"extra": "forbid"}
    query: str = Field(max_length=512)


class ParsedSearch(BaseModel):
    residual_query: str
    filters: ModelFilters = Field(default_factory=ModelFilters)
    sort: SearchSort = "relevance"
    parsed: bool = False
    reason: str | None = None
    timezone: str
