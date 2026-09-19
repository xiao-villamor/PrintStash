"""Preplaced model contracts are immutable, digest-checked and narrowly bounded."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Literal

from printstash_core.inference import EmbeddingError, EmbeddingSpace
from printstash_core.search.point_inputs import PointRecipe
from printstash_core.search.text_inputs import TextRecipe
from printstash_core.search.visual_inputs import VisualRecipe
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


class SentenceTower(FrozenContract):
    graph: ModelAsset
    tokenizer: ModelAsset
    input_name: SafeName = "input_ids"
    attention_mask_name: SafeName = "attention_mask"
    token_type_ids_name: SafeName | None = "token_type_ids"
    output_name: SafeName = "last_hidden_state"
    pooling: Literal["cls", "mean"] = "cls"
    max_tokens: int = Field(default=512, ge=2, le=512, strict=True)
    pad_id: int = Field(default=0, ge=0, le=2**31 - 1, strict=True)
    pad_token: str = Field(default="[PAD]", min_length=1, max_length=64)
    opset: int = Field(ge=7, le=25, strict=True)
    canary_text: str = Field(default="a 3D object", min_length=1, max_length=256)
    canary: tuple[Finite, ...] = Field(min_length=1, max_length=4096)


class TextModelManifest(FrozenContract):
    schema_version: Literal[2] = 2
    model_key: SafeName
    model_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    family: Literal["text"] = "text"
    native_dimension: int = Field(ge=1, le=4096, strict=True)
    normalization: Literal["l2"] = "l2"
    query_prefix: str = Field(default="", max_length=256)
    document_prefix: str = Field(default="", max_length=256)
    language: tuple[str, ...] = Field(min_length=1, max_length=128)
    license: str = Field(min_length=1, max_length=128)
    text: SentenceTower
    canary_tolerance: float = Field(default=0.0001, gt=0, le=0.001, allow_inf_nan=False)

    @model_validator(mode="after")
    def compatible_tower(self):
        if len(self.text.canary) != self.native_dimension:
            raise ValueError("canary_dimension_mismatch")
        return self

    def space(self) -> EmbeddingSpace:
        digest = hashlib.sha256(self.model_dump_json().encode()).hexdigest()
        return EmbeddingSpace(
            model_key=self.model_key,
            model_revision=self.model_revision,
            dimension=self.native_dimension,
            modality="text",
            render_recipe=TextRecipe(encoder_manifest_sha256=digest).encode(),
            profile="semantic_text",
            query_prefix=self.query_prefix,
            document_prefix=self.document_prefix,
            model_repo=self.repository,
        )

    def assets(self) -> tuple[ModelAsset, ...]:
        return self.text.graph, self.text.tokenizer


class PointTower(FrozenContract):
    graph: ModelAsset
    centers_name: SafeName = "centers"
    grouped_name: SafeName = "grouped"
    output_name: SafeName = "point_embeds"
    opset: Literal[17] = 17
    canary: tuple[Finite, ...] = Field(min_length=1, max_length=4096)


class PointModelManifest(FrozenContract):
    schema_version: Literal[3] = 3
    model_key: SafeName
    model_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    checkpoint_sha256: Digest
    license: str = Field(min_length=1, max_length=128)
    family: Literal["openshape_pointbert"] = "openshape_pointbert"
    paired: LocalModelManifest
    paired_space_hash: Digest
    point: PointTower
    canary_tolerance: float = Field(default=0.0001, gt=0, le=0.001, allow_inf_nan=False)

    @model_validator(mode="after")
    def compatible_towers(self):
        if (
            self.paired.family != "clip"
            or self.paired.text is None
            or self.paired_space_hash != self.paired.space().config_hash
            or len(self.point.canary) != self.paired.native_dimension
        ):
            raise ValueError("embedding_point_alignment_mismatch")
        names = [asset.filename for asset in self.assets()]
        if len(set(names)) != len(names):
            raise ValueError("embedding_asset_name_collision")
        return self

    @property
    def native_dimension(self) -> int:
        return self.paired.native_dimension

    @property
    def image(self) -> ImageTower:
        return self.paired.image

    @property
    def text(self) -> TextTower | None:
        return self.paired.text

    @property
    def query_prefix(self) -> str:
        return self.paired.query_prefix

    def space(self) -> EmbeddingSpace:
        digest = hashlib.sha256(self.model_dump_json().encode()).hexdigest()
        return EmbeddingSpace(
            model_key=self.model_key,
            model_revision=self.model_revision,
            dimension=self.native_dimension,
            modality="point_cloud",
            profile="point_cloud",
            render_recipe=PointRecipe(digest, self.paired_space_hash).encode(),
            model_repo=self.repository,
            alignment_identity=self.paired_space_hash,
        )

    def assets(self) -> tuple[ModelAsset, ...]:
        return (*self.paired.assets(), self.point.graph)


class SparseTerm(FrozenContract):
    term: str = Field(min_length=1, max_length=128, pattern=r"^[\w]+$")
    weight: float = Field(gt=0, le=10, allow_inf_nan=False)


class SparseModelManifest(FrozenContract):
    """Index-time MLM expansion; vocabulary coordinates are not a dense Space."""

    schema_version: Literal[4] = 4
    model_key: SafeName
    model_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    license: str = Field(min_length=1, max_length=128)
    language: tuple[str, ...] = Field(min_length=1, max_length=16)
    family: Literal["splade"] = "splade"
    graph: ModelAsset
    tokenizer: ModelAsset
    vocabulary_size: int = Field(ge=2, le=65536, strict=True)
    input_name: SafeName = "input_ids"
    attention_mask_name: SafeName = "input_mask"
    token_type_ids_name: SafeName = "segment_ids"
    output_name: SafeName = "output"
    opset: int = Field(ge=7, le=25, strict=True)
    max_tokens: int = Field(default=512, ge=2, le=512, strict=True)
    max_terms: int = Field(default=64, ge=1, le=128, strict=True)
    pooling: Literal["max-log1p-relu-attention-v1"] = "max-log1p-relu-attention-v1"
    term_recipe: Literal["whole-unicode-words-top-weight-v1"] = (
        "whole-unicode-words-top-weight-v1"
    )
    canary_text: str = Field(min_length=1, max_length=256)
    canary: tuple[SparseTerm, ...] = Field(min_length=1, max_length=128)
    canary_tolerance: float = Field(default=0.0001, gt=0, le=0.001, allow_inf_nan=False)

    @model_validator(mode="after")
    def compatible_assets(self):
        if self.graph.filename == self.tokenizer.filename:
            raise ValueError("embedding_asset_name_collision")
        if len(self.canary) > self.max_terms or len(
            {term.term for term in self.canary}
        ) != len(self.canary):
            raise ValueError("embedding_sparse_canary_invalid")
        return self

    @property
    def native_dimension(self) -> int:
        return self.vocabulary_size

    def assets(self) -> tuple[ModelAsset, ...]:
        return (self.graph, self.tokenizer)

    def space(self) -> EmbeddingSpace:
        raise EmbeddingError("embedding_sparse_not_dense")


ModelManifest = (
    LocalModelManifest | TextModelManifest | PointModelManifest | SparseModelManifest
)


def manifest_identity(manifest: ModelManifest) -> str:
    if isinstance(manifest, SparseModelManifest):
        return hashlib.sha256(manifest.model_dump_json().encode()).hexdigest()
    return manifest.space().config_hash


def validate_space(manifest: ModelManifest, space: EmbeddingSpace) -> None:
    expected = manifest.space()
    if isinstance(manifest, LocalModelManifest) and space.profile in {
        "thumbnail",
        "multiview",
    }:
        recipe = VisualRecipe.for_space(space)
        expected = VisualRecipe.space(
            expected,
            image_size=manifest.image.image_size,
            profile=recipe.profile,
            aggregation=recipe.aggregation,
        )
    elif isinstance(manifest, TextModelManifest):
        recipe = TextRecipe.for_space(space)
        original = TextRecipe.for_space(expected)
        if recipe.encoder_manifest_sha256 != original.encoder_manifest_sha256:
            raise EmbeddingError("embedding_space_mismatch")
        expected = replace(
            expected,
            render_recipe=space.render_recipe,
            query_prefix=space.query_prefix,
            document_prefix=space.document_prefix,
        )
    if expected != space:
        raise EmbeddingError("embedding_space_mismatch")


def read_manifest(directory: Path, model_key: str) -> ModelManifest:
    path = directory / "manifest.json"
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
            raise EmbeddingError("embedding_manifest_unavailable")
        with path.open("rb") as stream:
            payload = stream.read(256 * 1024 + 1)
        if len(payload) > 256 * 1024:
            raise EmbeddingError("embedding_manifest_unavailable")
        kind = json.loads(payload).get("schema_version", 1)
        manifest_type = {
            1: LocalModelManifest,
            2: TextModelManifest,
            3: PointModelManifest,
            4: SparseModelManifest,
        }.get(kind)
        if manifest_type is None:
            raise EmbeddingError("embedding_manifest_invalid")
        manifest = manifest_type.model_validate_json(payload)
    except (
        OSError,
        ValidationError,
        ValueError,
        AttributeError,
        RecursionError,
    ) as exc:
        raise EmbeddingError("embedding_manifest_invalid") from exc
    if manifest.model_key != model_key:
        raise EmbeddingError("embedding_model_key_mismatch")
    return manifest


def verify_assets(directory: Path, manifest: ModelManifest) -> None:
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
