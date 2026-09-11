"""Strict portable Family values; instance IDs and machine candidates never cross."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.schemas.family_types import VariantRole

ModelHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
PortableTag = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)
]


class PortableFamilyCover(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    entry: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(gt=0, le=15 * 1024 * 1024)
    sha256: ModelHash
    content_type: Literal["image/webp"] = "image/webp"


class PortableFamilyMember(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    model_hash: ModelHash
    role: VariantRole = "identical"
    transformation_note: str | None = Field(default=None, max_length=4096)
    scale_factor: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    relative_to_model_hash: ModelHash | None = None
    mirrored: bool = False
    mirror_verified: bool = False
    mirror_reference_model_hash: ModelHash | None = None
    relative_review_required: bool = False
    sort_order: int = Field(default=0, ge=0, le=1_000_000)


class PortableFamily(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    export_id: str = Field(min_length=36, max_length=36)
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1_000_000)
    collection: str | None = Field(default=None, max_length=512)
    tags: list[PortableTag] = Field(default_factory=list, max_length=100)
    canonical_model_hash: ModelHash | None = None
    cover_model_hash: ModelHash | None = None
    cover_image_url: str | None = Field(
        default=None, max_length=2083, pattern=r"^https?://"
    )
    cover: PortableFamilyCover | None = None
    members: list[PortableFamilyMember] = Field(
        default_factory=list, max_length=100_000
    )

    @field_validator("export_id")
    @classmethod
    def stable_uuid(cls, value: str) -> str:
        return str(UUID(value))

    @field_validator("name")
    @classmethod
    def nonblank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("family_name_required")
        return value.strip()

    @model_validator(mode="after")
    def unique_members(self) -> "PortableFamily":
        hashes = [member.model_hash for member in self.members]
        if len(hashes) != len(set(hashes)):
            raise ValueError("family_duplicate_members")
        canonical = [
            member.model_hash for member in self.members if member.role == "canonical"
        ]
        if canonical and canonical != [self.canonical_model_hash]:
            raise ValueError("family_canonical_invalid")
        if (
            self.cover is not None
            and self.cover.entry != f"family-covers/{self.export_id}.webp"
        ):
            raise ValueError("family_cover_invalid")
        return self
