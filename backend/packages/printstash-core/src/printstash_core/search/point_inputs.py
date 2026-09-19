"""Immutable point recipe; alignment is an exact paired encoder identity."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from printstash_core.inference import EmbeddingError, EmbeddingSpace
from printstash_core.inference.points import POINT_RECIPE


@dataclass(frozen=True)
class PointRecipe:
    encoder_space_hash: str
    paired_space_hash: str
    version: str = POINT_RECIPE
    profile: str = "point_cloud"

    def __post_init__(self):
        if (
            any(
                re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in (self.encoder_space_hash, self.paired_space_hash)
            )
            or self.version != POINT_RECIPE
            or self.profile != "point_cloud"
        ):
            raise EmbeddingError("embedding_point_recipe_invalid")

    def encode(self) -> str:
        return json.dumps(self.__dict__, sort_keys=True, separators=(",", ":"))

    @classmethod
    def for_space(cls, space: EmbeddingSpace) -> PointRecipe:
        try:
            recipe = cls(**json.loads(space.render_recipe))
        except (ValueError, TypeError):
            raise EmbeddingError("embedding_point_recipe_invalid") from None
        if (
            space.profile != recipe.profile
            or space.modality != "point_cloud"
            or space.alignment_identity != recipe.paired_space_hash
        ):
            raise EmbeddingError("embedding_point_recipe_invalid")
        return recipe
