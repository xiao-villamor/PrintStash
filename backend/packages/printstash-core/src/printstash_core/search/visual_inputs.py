"""Frozen visual recipes and aggregation over explicitly paired native towers."""

from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass, replace
from typing import Literal, cast

from printstash_core.inference import EmbeddingError, EmbeddingSpace
from printstash_core.inference.vectors import normalize
from printstash_core.mesh.preview_profile import PREVIEW_PROFILE

VisualProfile = Literal["thumbnail", "multiview"]
Aggregation = Literal["mean", "max"]


@dataclass(frozen=True)
class VisualRecipe:
    encoder_space_hash: str
    image_size: int
    profile: VisualProfile
    aggregation: Aggregation = "mean"
    version: str | None = None
    thumbnail_recipe: str = PREVIEW_PROFILE.recipe_fingerprint + "-w640"

    def __post_init__(self):
        expected_version = (
            "canonical-views-rust-rgb-v2"
            if self.profile == "multiview"
            else "canonical-views-media-thumbnail-v1"
        )
        if self.version is None:
            object.__setattr__(self, "version", expected_version)
        if (
            not isinstance(cast(object, self.encoder_space_hash), str)
            or re.fullmatch(r"[0-9a-f]{64}", self.encoder_space_hash) is None
            or type(self.image_size) is not int
            or not 32 <= self.image_size <= 512
            or self.profile not in {"thumbnail", "multiview"}
            or self.aggregation not in {"mean", "max"}
            or self.profile == "thumbnail"
            and self.aggregation != "mean"
            or self.version != expected_version
            or self.thumbnail_recipe != PREVIEW_PROFILE.recipe_fingerprint + "-w640"
        ):
            raise EmbeddingError("search_visual_recipe_invalid")

    @property
    def view_count(self) -> int:
        return 1 if self.profile == "thumbnail" else 6

    @property
    def view_identity(self) -> str:
        """Aggregation changes can reuse views; encoder or rendering changes cannot."""
        values = {k: v for k, v in self.__dict__.items() if k != "aggregation"}
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def encode(self) -> str:
        return json.dumps(self.__dict__, sort_keys=True, separators=(",", ":"))

    @classmethod
    def space(
        cls,
        encoder: EmbeddingSpace,
        *,
        image_size: int,
        profile: VisualProfile,
        aggregation: Aggregation = "mean",
    ) -> EmbeddingSpace:
        if encoder.modality != "text_image":
            raise EmbeddingError("embedding_alignment_unavailable")
        recipe = cls(encoder.config_hash, image_size, profile, aggregation)
        return replace(
            encoder,
            profile=profile,
            render_recipe=recipe.encode(),
            alignment_identity=encoder.config_hash,
        )

    @classmethod
    def for_space(cls, space: EmbeddingSpace) -> VisualRecipe:
        try:
            values = json.loads(space.render_recipe)
            if not isinstance(values, dict) or not isinstance(
                values.get("version"), str
            ):
                raise ValueError("missing explicit renderer version")
            recipe = cls(**values)
        except (TypeError, ValueError):
            raise EmbeddingError("search_visual_recipe_invalid") from None
        if (
            space.profile != recipe.profile
            or space.modality != "text_image"
            or space.alignment_identity != recipe.encoder_space_hash
        ):
            raise EmbeddingError("search_visual_recipe_invalid")
        return recipe


def mean_pool(
    vectors: tuple[tuple[float, ...], ...], dimension: int
) -> tuple[float, ...]:
    if not 1 <= len(vectors) <= 12:
        raise EmbeddingError("embedding_view_budget")
    views = [struct.unpack(f"<{dimension}f", normalize(v, dimension)) for v in vectors]
    mean = [sum(column) / len(views) for column in zip(*views, strict=True)]
    return struct.unpack(f"<{dimension}f", normalize(mean, dimension))
