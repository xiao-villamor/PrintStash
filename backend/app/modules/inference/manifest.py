"""Preplaced model contracts are immutable, digest-checked and narrowly bounded."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from printstash_core.inference import EmbeddingError, EmbeddingSpace
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SafeName = Annotated[
    str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
]
Finite = Annotated[float, Field(allow_inf_nan=False)]


class FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelAsset(FrozenContract):
    filename: SafeName
    sha256: Digest


class ImageTower(FrozenContract):
    graph: ModelAsset
    input_name: SafeName = "pixel_values"
    output_name: SafeName = "image_embeds"
    image_size: int = Field(default=224, ge=32, le=512, strict=True)
    mean: tuple[Finite, Finite, Finite] = (0.48145466, 0.4578275, 0.40821073)
    std: tuple[
        Annotated[float, Field(gt=0, le=10, allow_inf_nan=False)],
        Annotated[float, Field(gt=0, le=10, allow_inf_nan=False)],
        Annotated[float, Field(gt=0, le=10, allow_inf_nan=False)],
    ] = (0.26862954, 0.26130258, 0.27577711)
    canary: tuple[Finite, ...] = Field(min_length=1, max_length=4096)


class TextTower(FrozenContract):
    graph: ModelAsset
    tokenizer: ModelAsset
    input_name: SafeName = "input_ids"
    attention_mask_name: SafeName | None = None
    output_name: SafeName = "text_embeds"
    max_tokens: int = Field(default=77, ge=2, le=256, strict=True)
    pad_id: int = Field(default=0, ge=0, le=2**31 - 1, strict=True)
    pad_token: str = Field(default="<|endoftext|>", max_length=64)
    canary_text: str = Field(default="a 3D object", min_length=1, max_length=256)
    canary: tuple[Finite, ...] = Field(min_length=1, max_length=4096)


class LocalModelManifest(FrozenContract):
    schema_version: Literal[1] = 1
    model_key: SafeName
    model_revision: str = Field(min_length=1, max_length=128)
    family: Literal["clip", "dino"]
    native_dimension: int = Field(ge=1, le=4096, strict=True)
    normalization: Literal["l2"] = "l2"
    render_recipe: Literal["six-orthographic-matte-v1"] = "six-orthographic-matte-v1"
    query_prefix: str = Field(default="", max_length=256)
    document_prefix: str = Field(default="", max_length=256)
    image: ImageTower
    text: TextTower | None = None
    canary_tolerance: float = Field(default=0.0001, gt=0, le=0.001, allow_inf_nan=False)

    @model_validator(mode="after")
    def compatible_towers(self):
        if (self.family == "clip") != (self.text is not None):
            raise ValueError("clip_requires_two_towers")
        if len(self.image.canary) != self.native_dimension or (
            self.text is not None and len(self.text.canary) != self.native_dimension
        ):
            raise ValueError("canary_dimension_mismatch")
        return self

    def space(self) -> EmbeddingSpace:
        # Asset hashes, tensor names, preprocessing and canaries all participate.
        # A changed preplaced export never joins an existing native vector space.
        return EmbeddingSpace(
            model_key=self.model_key,
            model_revision=self.model_revision,
            dimension=self.native_dimension,
            modality="text_image" if self.text is not None else "image",
            render_recipe=json.dumps(
                {
                    "version": self.render_recipe,
                    "manifest_sha256": hashlib.sha256(
                        self.model_dump_json().encode()
                    ).hexdigest(),
                    "image_size": self.image.image_size,
                },
                sort_keys=True,
            ),
            query_prefix=self.query_prefix,
            document_prefix=self.document_prefix,
        )

    def assets(self) -> tuple[ModelAsset, ...]:
        return (
            (self.image.graph,)
            if self.text is None
            else (self.image.graph, self.text.graph, self.text.tokenizer)
        )


def read_manifest(directory: Path, model_key: str) -> LocalModelManifest:
    path = directory / "manifest.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
            raise EmbeddingError("embedding_manifest_unavailable")
        manifest = LocalModelManifest.model_validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise EmbeddingError("embedding_manifest_invalid") from exc
    if manifest.model_key != model_key:
        raise EmbeddingError("embedding_model_key_mismatch")
    return manifest


def verify_assets(directory: Path, manifest: LocalModelManifest) -> None:
    total = 0
    for asset in manifest.assets():
        path = directory / asset.filename
        try:
            size = path.stat().st_size
            if path.is_symlink() or not path.is_file() or not 0 < size <= 1024**3:
                raise EmbeddingError("embedding_asset_invalid")
            total += size
            if total > 1536 * 1024**2:
                raise EmbeddingError("embedding_asset_budget")
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError as exc:
            raise EmbeddingError("embedding_asset_unavailable") from exc
        if digest != asset.sha256:
            raise EmbeddingError("embedding_asset_digest_mismatch")
