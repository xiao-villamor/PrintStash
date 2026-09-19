"""Admin-only immutable endpoint proposals and secret-free disclosures."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.config import settings as environment
from app.core.timezones import timezone_name
from app.modules.inference.endpoint import EndpointParameters


class EndpointProposal(EndpointParameters):
    inherit_credentials_from_id: int | None = Field(default=None, ge=1)
    kind: Literal["embedding", "chat"] = "embedding"
    native_dimension: int | None = Field(default=None, ge=1, le=4096)
    supports_images: bool = False

    @model_validator(mode="after")
    def validate_kind(self):
        if self.kind == "embedding" and (
            self.native_dimension is None
            or self.supports_images
            or self.prefer_responses
        ):
            raise ValueError("embedding_text_dimension_required")
        if self.kind == "chat" and self.native_dimension is not None:
            raise ValueError("chat_dimension_forbidden")
        return self


class EndpointRead(BaseModel):
    id: int
    kind: str
    host: str
    base_url: str
    model: str
    revision: str
    model_repo: str | None
    mrl_dimensions: list[int]
    config_hash: str
    native_dimension: int | None
    supports_images: bool
    dialect: str | None
    guarantee: str | None
    has_credentials: bool
    header_names: list[str]
    timeout_seconds: float
    max_input_characters: int
    prefer_responses: bool


class SearchSettings(BaseModel):
    model_config = {"extra": "forbid", "frozen": True, "validate_default": True}
    enabled: bool = Field(default_factory=lambda: environment.ai_search_enabled)
    lexical_backend: Literal["auto", "ranked_like"] = Field(
        default_factory=lambda: environment.ai_search_lexical_backend
    )
    captions_enabled: bool = Field(
        default_factory=lambda: environment.ai_search_captions_enabled
    )
    nl_filters_enabled: bool = Field(
        default_factory=lambda: environment.ai_search_nl_filters_enabled
    )
    local_models_enabled: bool = Field(
        default_factory=lambda: environment.embedding_local_enabled
    )
    download_enabled: bool = Field(
        default_factory=lambda: environment.embedding_download_enabled
    )
    send_rendered_images: bool = Field(
        default_factory=lambda: environment.ai_search_send_rendered_images
    )
    send_query_images: bool = Field(
        default_factory=lambda: environment.ai_search_send_query_images
    )
    timezone: str = Field(
        default="UTC",
        max_length=128,
        description="Instance calendar timezone; personal preference takes precedence.",
    )

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value):
        return timezone_name(value)

    sparse_expansion_enabled: bool = False
    sparse_model_id: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None = None
    chat_endpoint_id: int | None = Field(default=None, ge=1)
    rollback_retention_hours: int = Field(default=24, ge=1, le=720)
    max_index_bytes: int = Field(default=2147483648, ge=1048576, le=1099511627776)
    query_timeout_seconds: float = Field(
        default_factory=lambda: environment.ai_search_query_timeout_seconds,
        ge=0.05,
        le=30,
    )
    semantic_floor: float = Field(
        default_factory=lambda: environment.ai_search_semantic_floor, ge=-1, le=1
    )
    lexical_weight: float = Field(
        default_factory=lambda: environment.ai_search_lexical_weight, gt=0, le=10
    )
    semantic_weight: float = Field(
        default_factory=lambda: environment.ai_search_semantic_weight, gt=0, le=10
    )
    rrf_k: int = Field(
        default_factory=lambda: environment.ai_search_rrf_k, ge=1, le=1000
    )
    semantic_floors: dict[
        Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")],
        Annotated[float, Field(ge=-1, le=1)],
    ] = Field(default_factory=dict, max_length=64)


class SearchSettingsRead(BaseModel):
    settings: SearchSettings
    endpoints: list[EndpointRead]
    environment_endpoints: list[Literal["embedding", "chat"]] = Field(
        default_factory=list
    )
