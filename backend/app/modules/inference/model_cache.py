"""One offline model inventory, with cross-process load/prune exclusion."""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from printstash_core.inference import EmbeddingError
from printstash_core.inference import EmbeddingSpace as Space
from printstash_core.inference.context import InferenceContext
from sqlmodel import Session, select

from app.core.config import settings
from app.db.models import EmbeddingSpace, IndexGeneration
from app.modules.inference.manifest import (
    ModelManifest,
    SparseModelManifest,
    manifest_identity,
    read_manifest,
    validate_space,
    verify_assets,
)
from app.modules.inference.worker_pool import pool


@dataclass(frozen=True)
class CachedModel:
    directory: Path
    manifest: ModelManifest
    size: int

    @property
    def id(self) -> str:
        return manifest_identity(self.manifest)


def safe_directory(directory: Path, *, create: bool = False) -> Path:
    directory = directory.absolute()
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise EmbeddingError("embedding_cache_path_invalid")
    if create:
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError:
            raise EmbeddingError("embedding_cache_unavailable") from None
    if not directory.is_dir():
        raise EmbeddingError("embedding_cache_unavailable")
    return directory


@contextmanager
def cache_lock(*, exclusive: bool = False, context: InferenceContext | None = None):
    root = safe_directory(settings.embedding_cache_dir, create=True)
    descriptor = os.open(
        root / ".cache.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    deadline = time.monotonic() + 5
    try:
        while True:
            if context is not None:
                context.remaining()
            try:
                fcntl.flock(
                    descriptor,
                    (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB,
                )
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise EmbeddingError("embedding_compute_busy") from None
                time.sleep(0.01)
        yield root
    finally:
        os.close(descriptor)


@contextmanager
def pin(directory: Path, *, context: InferenceContext | None = None):
    if directory.absolute().parent != settings.embedding_cache_dir.absolute():
        # The cache never deletes external custom directories, including
        # read-only air-gapped installations. Their administrator owns them.
        yield safe_directory(directory)
        return
    with cache_lock(context=context):
        yield safe_directory(directory)


def inspect(directory: Path) -> CachedModel:
    directory = safe_directory(directory)
    path = directory / "manifest.json"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 256 * 1024:
        raise EmbeddingError("embedding_manifest_unavailable")
    try:
        key = json.loads(path.read_bytes())["model_key"]
        manifest = read_manifest(directory, key)
        paths = [directory / asset.filename for asset in manifest.assets()]
        if any(path.is_symlink() or not path.is_file() for path in paths):
            raise EmbeddingError("embedding_asset_invalid")
        size = sum(path.stat().st_size for path in paths) + path.stat().st_size
        if size > 1536 * 1024**2:
            raise EmbeddingError("embedding_asset_budget")
    except (KeyError, TypeError, ValueError, RecursionError, OSError):
        raise EmbeddingError("embedding_manifest_invalid") from None
    return CachedModel(directory, manifest, size)


def inventory() -> tuple[CachedModel, ...]:
    root = settings.embedding_cache_dir
    directories = []
    if root.exists():
        safe_directory(root)
        for candidate in root.iterdir():
            if not candidate.name.startswith("."):
                directories.append(candidate)
                if len(directories) > 64:
                    raise EmbeddingError("embedding_cache_entry_limit")
    if settings.embedding_local_model_dir:
        directories.append(Path(settings.embedding_local_model_dir))
    result = []
    for directory in directories:
        try:
            result.append(inspect(directory))
        except EmbeddingError:
            continue
    return tuple(result)


def resolve(identity: str) -> CachedModel:
    if not re.fullmatch(r"[a-f0-9]{64}", identity):
        raise EmbeddingError("embedding_model_not_found")
    for model in inventory():
        if model.id == identity:
            return model
    raise EmbeddingError("embedding_model_not_found")


def for_space(space: Space) -> CachedModel:
    for model in inventory():
        try:
            validate_space(model.manifest, space)
        except EmbeddingError:
            continue
        return model
    raise EmbeddingError("embedding_model_not_found")


def referenced(session: Session, model: CachedModel) -> bool:
    if isinstance(model.manifest, SparseModelManifest):
        from app.modules.search.settings import settings as search_settings

        config = search_settings(session)
        return config.sparse_expansion_enabled and config.sparse_model_id == model.id
    rows = session.exec(
        select(EmbeddingSpace.config_json)
        .join(IndexGeneration, IndexGeneration.space_id == EmbeddingSpace.id)
        .where(EmbeddingSpace.provider == "onnx_cpu")
    ).all()
    for value in rows:
        try:
            validate_space(model.manifest, Space(**json.loads(value)))
        except (EmbeddingError, TypeError, ValueError):
            continue
        return True
    return False


def _remove(session: Session, model: CachedModel, root: Path):
    if (
        model.directory.parent != root
        or referenced(session, model)
        or not pool.discard_directory(model.directory)
    ):
        raise EmbeddingError("embedding_model_in_use")
    tombstone = root / (".prune-" + model.id)
    model.directory.rename(tombstone)
    shutil.rmtree(tombstone)


def remove(session: Session, identity: str):
    with cache_lock(exclusive=True) as root:
        _remove(session, resolve(identity), root)


def make_room(session: Session, required: int) -> None:
    """Caller holds the exclusive cache lock; staging bytes count toward quota."""
    root = safe_directory(settings.embedding_cache_dir)
    models = sorted(
        (model for model in inventory() if model.directory.parent == root),
        key=lambda model: model.directory.stat().st_mtime_ns,
    )
    # Unknown files and incomplete installs consume space too, and are never
    # silently evicted as if they were recognized model versions.
    occupied = 0
    count = 0
    for directory, subdirs, files in os.walk(root, followlinks=False):
        if any((Path(directory) / name).is_symlink() for name in (*subdirs, *files)):
            raise EmbeddingError("embedding_cache_path_invalid")
        for name in files:
            count += 1
            if count > 1024:
                raise EmbeddingError("embedding_cache_entry_limit")
            occupied += (Path(directory) / name).stat().st_size
    for model in models:
        if occupied + required <= settings.embedding_cache_max_bytes:
            break
        if not referenced(session, model):
            try:
                _remove(session, model, root)
            except EmbeddingError:
                continue
            occupied -= model.size
    if occupied + required > settings.embedding_cache_max_bytes:
        raise EmbeddingError("embedding_cache_budget")


def verify(identity: str) -> CachedModel:
    with cache_lock():
        model = resolve(identity)
        verify_assets(model.directory, model.manifest)
        return model
