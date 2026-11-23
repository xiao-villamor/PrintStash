from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator


class AutomaticCover(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["automatic"]


class ImageCover(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["image"]
    filename: str = Field(pattern=r"^[a-f0-9]{64}\.(?:jpg|png|gif|webp)$")


class ModelsCover(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["models"]
    model_ids: list[Annotated[int, Field(gt=0, strict=True)]] = Field(
        min_length=1, max_length=4
    )

    @field_validator("model_ids")
    @classmethod
    def unique_models(cls, ids: list[int]) -> list[int]:
        if len(ids) != len(set(ids)):
            raise ValueError("Cover models must be unique")
        return ids


CustomCover = Annotated[ImageCover | ModelsCover, Field(discriminator="kind")]
CollectionCover = Annotated[
    AutomaticCover | ImageCover | ModelsCover, Field(discriminator="kind")
]
custom_cover_adapter: TypeAdapter[CustomCover] = TypeAdapter(CustomCover)
