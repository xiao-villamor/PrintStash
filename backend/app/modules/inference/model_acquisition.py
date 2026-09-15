"""Explicit, bounded acquisition of registry files, with atomic publication."""

from __future__ import annotations

import fcntl
import hashlib
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx
from printstash_core.inference import EmbeddingError
from printstash_core.inference.context import InferenceContext

from app.core.config import settings
from app.db.session import SessionFactory
from app.modules.inference import model_cache, model_registry
from app.modules.inference.http_runtime import private_http
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.manifest import SparseModelManifest, verify_assets
from app.modules.inference.model_registry import DownloadAsset, RegistryEntry
from app.modules.inference.sparse import LocalSparseProvider
from app.modules.storage.capacity import CapacityManager, CapacityResource

_HOSTS = {
    # Explicit file-delivery hosts from https://huggingface.co/docs/hub/models-downloading.
    # Keep exact matches: a suffix wildcard would also admit unrelated subdomains.
    "huggingface.co",
    "cdn-lfs.huggingface.co",
    "cdn-lfs-us-1.huggingface.co",
    "cdn-lfs-us-1.hf.co",
    "cdn-lfs-eu-1.hf.co",
    "us.aws.cdn.hf.co",
    "us.gcp.cdn.hf.co",
    "cas-bridge.xethub.hf.co",
    "transfer.xethub.hf.co",
}


@dataclass(frozen=True)
class DownloadPolicy:
    mirror: str = ""

    def validate(self, url: str) -> str:
        try:
            parsed = urlsplit(url)
            mirror = urlsplit(self.mirror) if self.mirror else None
            allowed = parsed.hostname in _HOSTS and parsed.port in (None, 443)
            if mirror is not None:
                if (
                    mirror.scheme != "https"
                    or not mirror.hostname
                    or mirror.username
                    or mirror.password
                    or mirror.query
                    or mirror.fragment
                ):
                    raise ValueError
                allowed = allowed or (parsed.hostname, parsed.port) == (
                    mirror.hostname,
                    mirror.port,
                )
            if (
                parsed.scheme != "https"
                or not allowed
                or parsed.username
                or parsed.password
                or parsed.fragment
            ):
                raise ValueError
            if len(url) > 16384 or any(ord(char) < 32 for char in url):
                raise ValueError
        except ValueError:
            raise EmbeddingError("embedding_download_url_forbidden") from None
        return url

    def source(self, entry: RegistryEntry, asset: DownloadAsset) -> str:
        base = self.mirror.rstrip("/") if self.mirror else "https://huggingface.co"
        return self.validate(
            f"{base}/{entry.repository}/resolve/{entry.revision}/{asset.source}"
        )


def fetch(
    client: httpx.Client,
    policy: DownloadPolicy,
    url: str,
    asset: DownloadAsset,
    destination: Path,
    check: Callable[[], None],
    progress: Callable[[int], None],
) -> None:
    """All redirects obey the same allowlist; actual bytes are authoritative."""
    for _ in range(6):
        check()
        policy.validate(url)
        with client.stream(
            "GET", url, headers={"Accept-Encoding": "identity"}, follow_redirects=False
        ) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise EmbeddingError("embedding_download_redirect_invalid")
                url = policy.validate(urljoin(url, location))
                continue
            if response.status_code != 200:
                raise EmbeddingError("embedding_download_failed")
            declared = response.headers.get("content-length")
            if declared is not None and (
                not declared.isascii()
                or not declared.isdigit()
                or len(declared) > 12
                or int(declared) > asset.size
            ):
                raise EmbeddingError("embedding_download_size_invalid")
            if response.headers.get("content-encoding", "identity") != "identity":
                raise EmbeddingError("embedding_download_encoding_invalid")
            digest, received = hashlib.sha256(), 0
            descriptor = os.open(
                destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(descriptor, "wb") as output:
                for chunk in response.iter_raw(chunk_size=65536):
                    check()
                    received += len(chunk)
                    if received > asset.size:
                        raise EmbeddingError("embedding_download_size_invalid")
                    digest.update(chunk)
                    output.write(chunk)
                    progress(len(chunk))
                output.flush()
                os.fsync(output.fileno())
            if received != asset.size:
                raise EmbeddingError("embedding_download_size_invalid")
            if digest.hexdigest() != asset.sha256:
                raise EmbeddingError("embedding_asset_digest_mismatch")
            return
    raise EmbeddingError("embedding_download_redirect_limit")


class Acquisition:
    def __init__(self, sessions: SessionFactory):
        self.sessions = sessions

    def install(
        self,
        key: str,
        *,
        enabled: Callable[[], bool],
        cancelled: Callable[[], bool] = lambda: False,
        progress: Callable[[int], None] = lambda count: None,
    ) -> model_cache.CachedModel:
        deadline = time.monotonic() + 1800

        def check():
            if cancelled() or not enabled():
                raise EmbeddingError("embedding_download_cancelled")
            if time.monotonic() > deadline:
                raise EmbeddingError("embedding_download_timeout")

        if not enabled():
            raise EmbeddingError("embedding_download_disabled")
        entry = model_registry.require(key)
        policy = DownloadPolicy(settings.embedding_mirror_url)
        for asset in entry.files:
            policy.source(entry, asset)
        root = model_cache.safe_directory(settings.embedding_cache_dir, create=True)
        # One transfer per cache volume across processes. Holding this lease
        # permits recovery of abandoned staging directories without a time guess.
        lock = os.open(
            root / ".download.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        staging = None
        reservation = None
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise EmbeddingError("embedding_download_busy") from None
            with model_cache.cache_lock(exclusive=True):
                for path in root.iterdir():
                    if re.fullmatch(r"\.(download|prune)-[0-9a-f]{32,64}", path.name):
                        if path.is_symlink():
                            raise EmbeddingError("embedding_cache_path_invalid")
                        shutil.rmtree(path)
                target = root / entry.id
                if target.exists():
                    existing = model_cache.inspect(target)
                    if existing.id != entry.id:
                        raise EmbeddingError("embedding_space_mismatch")
                    verify_assets(target, entry.manifest)
                    return existing
                with self.sessions.scoped_session() as session:
                    model_cache.make_room(session, entry.size)
                reservation = CapacityManager(self.sessions).reserve(
                    "model-download:" + uuid4().hex,
                    [
                        CapacityResource.for_path(
                            root, entry.size, role="local inference models"
                        )
                    ],
                )
                staging = root / (".download-" + uuid4().hex)
                staging.mkdir(mode=0o700)
            with (
                private_http(),
                httpx.Client(
                    timeout=httpx.Timeout(5), trust_env=False, follow_redirects=False
                ) as client,
            ):
                for asset in entry.files:
                    check()
                    if not re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", asset.filename
                    ):
                        raise EmbeddingError("embedding_cache_path_invalid")
                    fetch(
                        client,
                        policy,
                        policy.source(entry, asset),
                        asset,
                        staging / asset.filename,
                        check,
                        progress,
                    )
            (staging / "manifest.json").write_text(entry.manifest.model_dump_json())
            verify_assets(staging, entry.manifest)
            (
                LocalSparseProvider
                if isinstance(entry.manifest, SparseModelManifest)
                else LocalEmbeddingProvider
            )(
                self.sessions,
                staging,
                entry.manifest.model_key,
                settings.embedding_onnx_threads,
            ).validate(
                context=InferenceContext.bounded(
                    120,
                    priority="background",
                    cancelled=lambda: cancelled() or not enabled(),
                )
            )
            check()
            with model_cache.cache_lock(exclusive=True):
                check()
                # A canary may have left a warm child pointing at staging.
                from app.modules.inference.worker_pool import pool

                if not pool.discard_directory(staging):
                    raise EmbeddingError("embedding_compute_busy")
                for path in staging.iterdir():
                    with path.open("rb") as stream:
                        os.fsync(stream.fileno())
                staging.rename(target)
                staging = None
                descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
            return model_cache.inspect(target)
        except EmbeddingError:
            raise
        except (OSError, httpx.HTTPError, ValueError):
            raise EmbeddingError("embedding_download_failed") from None
        finally:
            if staging is not None:
                from app.modules.inference.worker_pool import pool

                pool.discard_directory(staging)
                shutil.rmtree(staging, ignore_errors=True)
            if reservation is not None:
                reservation.release()
            os.close(lock)
