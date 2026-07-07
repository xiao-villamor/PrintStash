"""Process-wide configuration, sourced from ``VAULT_*`` environment variables.

Frozen env-only settings are wrapped by ``ConfigResolver`` which layers
runtime overrides (DB-backed) on top. See ADR-0002.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine.url import make_url

# ---------------------------------------------------------------------------
# Shared overlay dict — written by runtime_config, read by ConfigResolver.
# Protected by _overlay_lock for writes; reads are GIL-safe dict lookups.
# ---------------------------------------------------------------------------

_overlay: dict[str, Any] = {}
_overlay_lock = asyncio.Lock()


class Settings(BaseSettings):
    """Frozen env-only settings. Never mutated after import.

    Runtime overrides live in the ``_overlay`` dict; the ``ConfigResolver``
    exposes the effective value (overlay wins, frozen falls back).
    """

    model_config = SettingsConfigDict(
        env_prefix="VAULT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    storage_backend: str = "local"
    data_dir: Path = Path("/data/files")
    thumb_dir: Path = Path("/data/thumbs")
    staging_dir: Path = Path("/data/staging")

    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "auto"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_presigned_url_expire_seconds: int = 900
    s3_multipart_threshold_mb: int = 50
    s3_lifecycle_expiration_days: int = 0
    s3_lifecycle_transition_days: int = 0
    s3_transition_storage_class: str = "STANDARD_IA"

    db_url: str = "sqlite:////data/db/printstash.sqlite"

    jwt_secret: str = "changeme_jwt_secret_please_change"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    # "Remember me" login lifetime. Kept short because the access token is a
    # stateless JWT that can't be revoked before it expires; operators who want
    # longer sessions can raise VAULT_REMEMBER_ME_DAYS.
    remember_me_days: int = 2
    # Short-lived token embedded in slicer ("Open in slicer") download URLs so an
    # external slicer process can fetch the file without the user's login session.
    slicer_download_token_expire_minutes: int = 15
    cors_origins: str = ""

    max_upload_mb: int = 512
    log_level: str = "INFO"

    # Static ceiling on mesh density for geometry extraction + thumbnail
    # rendering. Loading + rasterising a mesh peaks (measured) at ~0.8–2 GB of RSS
    # per million triangles for STL/PLY/OBJ and ~3–4 GB/M for 3MF (its XML loader
    # is far heavier) — paid mostly inside trimesh.load and our rasteriser, so a
    # dense model can OOM-kill a library scan (issues #24/#29). Above this estimate
    # the mesh is not loaded; the file is still indexed, and 3MF still gets its
    # embedded slicer preview. This is the hard ceiling; the RAM-aware cap below
    # tightens it further on small hosts.
    mesh_max_render_triangles: int = 2_000_000

    # Fraction of detected available RAM that a single mesh load+render may peak
    # to. The effective triangle cap is derived from this (per format, using the
    # measured per-triangle peak cost), divided by ``max_render_jobs`` so the
    # budget is shared across concurrent renders, and combined with the static
    # ceiling above via a min(), so a small 4 GB container automatically skips
    # meshes a 32 GB host would happily render — no per-host tuning needed to keep
    # a scan from being OOM-killed (#29). Container-aware: honours the cgroup
    # memory limit, not just host RAM. Set to 0 to disable RAM-aware capping. 0.5
    # leaves headroom for the rest of the app and the OS while still rendering
    # typical detailed models; 0.30–0.35 is safer for production / self-hosted
    # setups that run other workloads alongside the scan.
    mesh_memory_budget_fraction: float = 0.5

    # Maximum number of mesh load+render jobs allowed to run at once. Ingestion
    # runs in FastAPI's background-task threadpool, so a bulk/folder upload (#26)
    # can otherwise fire dozens of concurrent renders that each peak hundreds of
    # MB and collectively OOM the box. This bounds concurrency two ways: a
    # semaphore caps how many renders run simultaneously, and the RAM-aware
    # triangle cap divides its budget by this count so each concurrent job stays
    # within its share. 1 (serialised) is the safe default; raise it on hosts with
    # RAM headroom. Values <= 0 are treated as 1.
    max_render_jobs: int = 1

    # Number of faces processed per chunk in the software rasteriser. The renderer
    # builds its per-face geometry/shading arrays (each O(faces)) one chunk at a
    # time and frees them before the next, so peak render memory is O(chunk_size)
    # rather than O(total_faces) — a million-triangle mesh no longer materialises
    # ~70 MB float32 arrays all at once (#29). Lower it to shrink peak RSS further
    # on tiny containers; raise it for marginally less Python-loop overhead.
    mesh_render_face_chunk_size: int = 200_000

    # For large 3MF files, prefer the slicer-embedded preview before handing the
    # archive to trimesh, whose XML loader is the dominant memory cost. When on
    # (default), a 3MF whose estimate exceeds the adaptive cap uses its embedded
    # preview directly and never decompresses/parses the mesh. Off restores the
    # previous load-then-fallback behaviour.
    use_embedded_3mf_preview_for_large_files: bool = True

    # Hard ceiling on the on-disk size of a mesh file we will hand to trimesh.
    # The triangle estimate above is format-specific and can come up empty — a
    # 3MF with no parseable <triangle>/.model parts, an unfamiliar header, a
    # compressed container whose mesh lives somewhere the estimator doesn't sum.
    # When it can't estimate, the old code loaded the file anyway, and the OOM is
    # paid *inside* trimesh.load: a ~900 MB 3MF decompresses into tens of GB of
    # mesh and OOM-kills the scan (issue #29). This byte cap is the format-blind
    # backstop: above it the mesh is never loaded — the file is still indexed and
    # a 3MF still gets its embedded slicer preview. 0 disables the size guard.
    mesh_max_load_mb: int = 200

    # Optional static bearer token guarding the Prometheus /metrics endpoint.
    # Empty = open on the trusted internal network (see docs/known-limitations).
    metrics_token: str = ""

    # URL + ZIP import (see services/importer.py).
    url_import_max_redirects: int = 5
    max_archive_entries: int = 500
    max_archive_entry_mb: int = 512
    max_archive_uncompressed_mb: int = 2048

    # Headless-browser fallback for Cloudflare-gated imports (MakerWorld). When
    # enabled, pages that return the bot challenge are re-fetched with Chromium
    # which solves the challenge automatically. See services/browser_fetch.py.
    makerworld_browser_enabled: bool = True
    makerworld_browser_headless: bool = True
    browser_fetch_timeout_seconds: int = 45

    # Instance-level MakerWorld session cookie. MakerWorld auth-gates file
    # downloads, so anonymous URL import can list a collection but never fetch its
    # files. Setting this once (admin pastes a logged-in `k=v; k2=v2` cookie
    # header) lets every import reuse it, so end users paste nothing. A per-request
    # `makerworld_cookie` still overrides it. Sessions expire — when downloads
    # start failing with `makerworld_login_required`, re-paste a fresh value.
    makerworld_cookie: str = ""

    backup_dir: Path = Path("/data/backups")
    backup_retention_days: int = 30
    trash_retention_days: int = 30

    backup_s3_bucket: str = ""
    backup_s3_endpoint_url: str = ""
    backup_s3_region: str = "auto"
    backup_s3_access_key: str = ""
    backup_s3_secret_key: str = ""

    app_name: str = "PrintStash"
    app_version: str = "0.8.1"

    @property
    def incoming_dir(self) -> Path:
        return self.staging_dir / "_incoming"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


class ConfigResolver:
    """Single read-path for effective configuration: overlay wins, frozen falls back.

    Wraps the frozen ``Settings`` and the shared ``_overlay`` dict so callers
    keep writing ``settings.data_dir`` — no migration churn at 16+ call sites.
    """

    __slots__ = ("_frozen",)

    def __init__(self, frozen: Settings) -> None:
        object.__setattr__(self, "_frozen", frozen)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        overlay_val = _overlay.get(name)
        if overlay_val is not None:
            return overlay_val
        return getattr(self._frozen, name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError("ConfigResolver is read-only — use overlay dict for mutations")

    @property
    def incoming_dir(self) -> Path:
        staging = _overlay.get("staging_dir", self._frozen.staging_dir)
        return staging / "_incoming"

    @property
    def max_upload_bytes(self) -> int:
        max_mb = _overlay.get("max_upload_mb", self._frozen.max_upload_mb)
        return max_mb * 1024 * 1024


# Public: same name, new type — transparent to all existing call sites.
settings = ConfigResolver(Settings())

# Expose frozen Settings class for introspection (defaults, model_fields).
FrozenSettings = Settings


def get_config() -> ConfigResolver:
    """Explicit accessor for the effective config resolver (alias for ``settings``)."""
    return settings


def _sqlite_db_path(db_url: str) -> Path | None:
    """Return the on-disk path for a sqlite URL, or ``None`` for in-memory/other."""
    if not db_url.startswith("sqlite"):
        return None
    database = make_url(db_url).database
    if not database or database == ":memory:":
        return None
    return Path(database)


def ensure_dirs() -> None:
    """Create required storage directories at startup. Idempotent."""
    settings.staging_dir.mkdir(parents=True, exist_ok=True)
    settings.incoming_dir.mkdir(parents=True, exist_ok=True)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)

    if settings.storage_backend == "local":
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.thumb_dir.mkdir(parents=True, exist_ok=True)

    db_path = _sqlite_db_path(settings.db_url)
    if db_path is not None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
