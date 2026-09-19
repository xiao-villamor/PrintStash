"""Reviewed model capabilities matched by immutable repository revision, never an alias."""

from dataclasses import dataclass

from .embedding import EmbeddingSpace


@dataclass(frozen=True)
class ModelCapabilities:
    repository: str
    revision: str
    native_dimension: int
    mrl_dimensions: tuple[int, ...]
    query_prefix: str
    document_prefix: str
    pooling: str
    languages: tuple[str, ...]
    license: str
    reference: str


# Source: the publisher's pinned model card and its measured MRL table.
# Acquisition metadata is separate: capability recognition never downloads or
# executes model code, and equal dimensions alone never match this registry.
MXBAI_LARGE_V1 = ModelCapabilities(
    repository="mixedbread-ai/mxbai-embed-large-v1",
    revision="b33106f585b9ce46904ad7443a3b52b7a63e231c",
    native_dimension=1024,
    mrl_dimensions=(64, 128, 256, 512),
    query_prefix="Represent this sentence for searching relevant passages: ",
    document_prefix="",
    pooling="cls",
    languages=("en",),
    license="Apache-2.0",
    reference="https://www.mixedbread.com/blog/binary-mrl",
)

REVIEWED_MODELS = (MXBAI_LARGE_V1,)


def capabilities_for(space: EmbeddingSpace) -> ModelCapabilities | None:
    return capabilities_for_identity(
        space.model_repo, space.model_revision, space.dimension
    )


def capabilities_for_identity(
    repository: str | None, revision: str, native_dimension: int | None
) -> ModelCapabilities | None:
    return next(
        (
            entry
            for entry in REVIEWED_MODELS
            if (entry.repository, entry.revision, entry.native_dimension)
            == (repository, revision, native_dimension)
        ),
        None,
    )
