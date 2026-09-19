"""Reviewed, immutable export contracts. Importing the catalog performs no I/O."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from printstash_core.inference import EmbeddingError

from app.modules.inference.manifest import (
    LocalModelManifest,
    ModelManifest,
    SparseModelManifest,
    TextModelManifest,
    manifest_identity,
)


@dataclass(frozen=True)
class DownloadAsset:
    filename: str
    source: str
    size: int
    sha256: str


@dataclass(frozen=True)
class RegistryEntry:
    manifest: ModelManifest
    files: tuple[DownloadAsset, ...]
    visual_repository: str | None = None
    visual_revision: str | None = None
    visual_license: str | None = None
    visual_languages: tuple[str, ...] = ()

    @property
    def repository(self) -> str:
        value = (
            self.manifest.repository
            if isinstance(self.manifest, (TextModelManifest, SparseModelManifest))
            else self.visual_repository
        )
        if value is None:
            raise EmbeddingError("embedding_registry_invalid")
        return value

    @property
    def revision(self) -> str:
        value = (
            self.manifest.model_revision
            if isinstance(self.manifest, (TextModelManifest, SparseModelManifest))
            else self.visual_revision
        )
        if value is None:
            raise EmbeddingError("embedding_registry_invalid")
        return value

    @property
    def license(self) -> str | None:
        return (
            self.manifest.license
            if isinstance(self.manifest, (TextModelManifest, SparseModelManifest))
            else self.visual_license
        )

    @property
    def languages(self) -> tuple[str, ...]:
        return (
            self.manifest.language
            if isinstance(self.manifest, (TextModelManifest, SparseModelManifest))
            else self.visual_languages
        )

    @property
    def id(self) -> str:
        return manifest_identity(self.manifest)

    @property
    def size(self) -> int:
        return sum(asset.size for asset in self.files) + len(
            self.manifest.model_dump_json().encode()
        )


@lru_cache(maxsize=1)
def entries() -> tuple[RegistryEntry, ...]:
    manifest = TextModelManifest.model_validate_json(
        (Path(__file__).parent / "registry" / "bge-small-en-v1.5.json").read_bytes()
    )
    clip = LocalModelManifest.model_validate_json(
        (
            Path(__file__).parent / "registry" / "clip-vit-base-patch32-fp32.json"
        ).read_bytes()
    )
    sparse = SparseModelManifest.model_validate_json(
        (Path(__file__).parent / "registry" / "splade-pp-en-v1.json").read_bytes()
    )
    return (
        RegistryEntry(
            sparse,
            (
                DownloadAsset(
                    "model.onnx", "onnx/model.onnx", 532126852, sparse.graph.sha256
                ),
                DownloadAsset(
                    "tokenizer.json", "tokenizer.json", 711649, sparse.tokenizer.sha256
                ),
            ),
        ),
        RegistryEntry(
            manifest,
            (
                DownloadAsset(
                    "model.onnx",
                    "onnx/model.onnx",
                    133093490,
                    manifest.text.graph.sha256,
                ),
                DownloadAsset(
                    "tokenizer.json",
                    "tokenizer.json",
                    711396,
                    manifest.text.tokenizer.sha256,
                ),
            ),
        ),
        RegistryEntry(
            clip,
            (
                DownloadAsset(
                    "vision_model.onnx",
                    "onnx/vision_model.onnx",
                    351685709,
                    clip.image.graph.sha256,
                ),
                DownloadAsset(
                    "text_model.onnx",
                    "onnx/text_model.onnx",
                    254058553,
                    clip.text.graph.sha256,
                ),
                DownloadAsset(
                    "tokenizer.json",
                    "tokenizer.json",
                    2224119,
                    clip.text.tokenizer.sha256,
                ),
            ),
            visual_repository="Xenova/clip-vit-base-patch32",
            visual_revision="d15189d7028b43f1d3e65039190477f6af591c2a",
            visual_license="MIT",
            visual_languages=("en",),
        ),
    )


def require(key: str) -> RegistryEntry:
    for entry in entries():
        if key in (entry.id, entry.manifest.model_key):
            return entry
    raise EmbeddingError("embedding_model_not_found")
