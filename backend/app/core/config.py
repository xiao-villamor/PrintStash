"""Process-wide configuration, sourced from ``VAULT_*`` environment variables.

Frozen env-only settings are wrapped by ``ConfigResolver`` which layers
runtime overrides (DB-backed) on top. See ADR-0002.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine.url import make_url

# ---------------------------------------------------------------------------
# Shared overlay dict — written by runtime_config, read by ConfigResolver.
# Protected by _overlay_lock for writes; reads are GIL-safe dict lookups.
# ---------------------------------------------------------------------------

_overlay: dict[str, Any] = {}
_overlay_lock = asyncio.Lock()

# The secret shipped in .env.example and the compose defaults. Public knowledge,
# therefore never usable: ``runtime_config.ensure_jwt_secret`` replaces it with a
# generated one on first boot.
DEFAULT_JWT_SECRET = "changeme_jwt_secret_please_change"

# Headroom the whole-request ceiling gets over the per-file cap.
#
# `max_upload_mb` is a limit on one *file* — that is what it is called in the UI
# and what a user reads it as. A multipart request carrying a file at the cap is
# necessarily larger than the file: boundaries, part headers, and the form fields
# beside it (`model_name`, `collection`, `tags`). With one number for both, the
# outer ceiling always fired first and the per-file guard could never run, so a
# file *at* the documented limit was rejected as `request_too_large` — and
# nothing could ever answer `upload_too_large`.
#
# 16 MiB is far more than any part header set, and small enough that the outer
# ceiling still bounds what a lying `content-length` or an endless stream can
# make the process buffer.
MULTIPART_OVERHEAD_BYTES = 16 * 1024 * 1024


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
    s3_presigned_url_expire_seconds: int = Field(default=900, gt=0)
    s3_multipart_threshold_mb: int = Field(default=50, gt=0)
    # Zero disables the corresponding lifecycle action.
    s3_lifecycle_expiration_days: int = Field(default=0, ge=0)
    s3_lifecycle_transition_days: int = Field(default=0, ge=0)
    s3_transition_storage_class: str = "STANDARD_IA"

    db_url: str = "sqlite:////data/db/printstash.sqlite"
    sqlite_synchronous: str = "NORMAL"
    sqlite_busy_timeout_ms: int = Field(default=30_000, ge=1)

    jwt_secret: str = DEFAULT_JWT_SECRET
    # First-run setup credential. When empty, a random process-local token is
    # generated and printed to the API log while the vault is unconfigured.
    setup_token: str = ""
    # Credentials persisted in the database are encrypted with this external
    # key. Empty uses a generated 0600 key file beside the SQLite database.
    secrets_key: str = ""
    secrets_key_file: Path = Path("/data/db/.printstash-secrets-key")
    session_cookie_secure: bool = False
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default=60, gt=0)
    # "Remember me" login lifetime. Kept short because the access token is a
    # stateless JWT that can't be revoked before it expires; operators who want
    # longer sessions can raise VAULT_REMEMBER_ME_DAYS.
    remember_me_days: int = Field(default=2, gt=0)
    # Generic OpenID Connect login. Disabled by default so local username/password
    # remains the zero-configuration, local-first path.
    oidc_enabled: bool = False
    oidc_issuer_url: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid profile email groups"
    oidc_username_claim: str = "preferred_username"
    oidc_groups_claim: str = "groups"
    oidc_admin_groups: str = "printstash-admins"
    oidc_display_name: str = "Single sign-on"
    oidc_redirect_uri: str = ""
    oidc_allow_insecure_http: bool = False
    # MyMiniFactory OAuth application credentials.  Both values are redacted by
    # Pydantic's SecretStr in settings dumps, exceptions, and repr output.
    mmf_client_id: SecretStr | None = None
    mmf_client_secret: SecretStr | None = None
    # Short-lived token embedded in slicer ("Open in slicer") download URLs so an
    # external slicer process can fetch the file without the user's login session.
    slicer_download_token_expire_minutes: int = Field(default=15, gt=0)
    cors_origins: str = ""

    max_upload_mb: int = Field(default=512, gt=0)
    portable_manifest_max_mb: int = Field(default=128, gt=0)
    staging_max_pending: int = Field(default=32, gt=0)
    staging_max_active_per_user: int = Field(default=4, gt=0)
    staging_max_gb: int = Field(default=4, gt=0)
    staging_min_free_gb: int = Field(default=1, ge=0)
    # Browser captures remain available for review; once importing begins the
    # shorter worker lease bounds abandoned staged bytes.
    staging_review_lease_days: int = Field(default=30, gt=0)
    staging_import_lease_hours: int = Field(default=24, gt=0)
    fleet_batch_max_quantity: int = Field(default=100, gt=0)
    ingest_worker_count: int = Field(default=2, gt=0)
    media_worker_timeout_seconds: int = Field(default=180, gt=0)
    # Best-effort archive ceiling for files recovered from a Bambu printer's
    # short-lived FTPS cache. Zero disables automatic external-job capture.
    bambu_external_capture_max_mb: int = Field(default=256, ge=0)
    # On-demand 3MF embedded toolpath preview limits. The service reads at
    # most cap+1 bytes and rejects high-compression-ratio members before read.
    three_mf_preview_max_uncompressed_mb: int = Field(default=32, gt=0)
    three_mf_preview_max_archive_mb: int = Field(default=128, gt=0)
    three_mf_preview_max_entries: int = Field(default=10_000, gt=0)
    three_mf_preview_max_central_directory_mb: int = Field(default=8, gt=0)
    three_mf_preview_max_ratio: float = Field(default=100.0, gt=0)
    three_mf_preview_max_concurrent: int = Field(default=2, gt=0)
    # Outbound metadata providers are deliberately capped even when an operator
    # raises related application limits. These settings only allow tightening
    # the safe defaults; retry and redirect handling stays in the transport.
    capture_provider_max_attempts: int = Field(default=3, ge=1, le=3)
    capture_provider_connect_timeout_seconds: float = Field(default=5, gt=0, le=5)
    capture_provider_total_timeout_seconds: float = Field(default=30, gt=0, le=30)
    capture_provider_concurrency: int = Field(default=4, ge=1, le=4)
    capture_provider_retry_after_max_seconds: float = Field(default=10, ge=0, le=10)
    log_level: str = "INFO"

    # Static ceiling on mesh density for geometry extraction + thumbnail
    # rendering. Loading + rasterising a mesh peaks (measured) at ~0.8–2 GB of RSS
    # per million triangles for STL/PLY/OBJ and ~3–4 GB/M for 3MF (its XML loader
    # is far heavier) — paid mostly inside trimesh.load_mesh and our rasteriser, so a
    # dense model can OOM-kill a library scan (issues #24/#29). Above this estimate
    # the mesh is not loaded; the file is still indexed, and 3MF still gets its
    # embedded slicer preview. This is the hard ceiling; the RAM-aware cap below
    # tightens it further on small hosts.
    mesh_max_render_triangles: int = Field(default=2_000_000, gt=0)

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
    mesh_memory_budget_fraction: float = Field(default=0.5, ge=0, le=1)

    # Maximum number of mesh load+render jobs allowed to run at once. Ingestion
    # runs in FastAPI's background-task threadpool, so a bulk/folder upload (#26)
    # can otherwise fire dozens of concurrent renders that each peak hundreds of
    # MB and collectively OOM the box. This bounds concurrency two ways: a
    # semaphore caps how many renders run simultaneously, and the RAM-aware
    # triangle cap divides its budget by this count so each concurrent job stays
    # within its share. 1 (serialised) is the safe default; raise it on hosts with
    # RAM headroom. Zero is the supported sentinel for serial execution.
    max_render_jobs: int = Field(default=1, ge=0)

    # Number of faces processed per chunk in the software rasteriser. The renderer
    # builds its per-face geometry/shading arrays (each O(faces)) one chunk at a
    # time and frees them before the next, so peak render memory is O(chunk_size)
    # rather than O(total_faces) — a million-triangle mesh no longer materialises
    # ~70 MB float32 arrays all at once (#29). Lower it to shrink peak RSS further
    # on tiny containers; raise it for marginally less Python-loop overhead.
    mesh_render_face_chunk_size: int = Field(default=200_000, gt=0)

    # Width of generated Model preview images. Height keeps the renderer's 4:3
    # aspect ratio. The Settings UI offers bounded presets so higher fidelity is
    # an explicit CPU/RAM/storage tradeoff on self-hosted machines.
    model_thumbnail_width: int = Field(default=640, ge=320, le=1280)

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
    # paid *inside* trimesh.load_mesh: a ~900 MB 3MF decompresses into tens of GB of
    # mesh and OOM-kills the scan (issue #29). This byte cap is the format-blind
    # backstop: above it the mesh is never loaded — the file is still indexed and
    # a 3MF still gets its embedded slicer preview. 0 disables the size guard.
    mesh_max_load_mb: int = Field(default=200, ge=0)

    # STEP tessellation runs in a disposable child process because its triangle
    # count is unknowable before Cascadio loads it. The child is killed on this
    # deadline; its RSS budget is derived from mesh_memory_budget_fraction and
    # the detected cgroup/host limit, just like other mesh work.
    mesh_step_timeout_seconds: int = Field(default=90, gt=0)

    # Oversized STL previews run in a disposable, streaming worker. The worker
    # deadline is intentionally capped by the service so an operator override
    # cannot leave an ingestion thread waiting indefinitely.
    mesh_stream_timeout_seconds: int = Field(default=45, gt=0, le=45)

    # Optional static bearer token guarding the Prometheus /metrics endpoint.
    # Empty = open on the trusted internal network (see docs/known-limitations).
    metrics_token: str = ""

    # URL + ZIP import (see services/importer.py).
    url_import_max_redirects: int = Field(default=5, ge=0)
    max_archive_entries: int = Field(default=500, gt=0)
    max_archive_entry_mb: int = Field(default=512, gt=0)
    max_archive_uncompressed_mb: int = Field(default=2048, gt=0)
    max_archive_central_directory_mb: int = Field(default=32, gt=0)
    max_archive_depth: int = Field(default=32, gt=0)
    max_archive_path_bytes: int = Field(default=1024, gt=0)

    # Deprecated compatibility input. MakerWorld files are transferred by the
    # browser extension and this value is never used for network requests.
    makerworld_cookie: str = ""

    backup_dir: Path = Path("/data/backups")
    # Zero means eligible for cleanup immediately; negative retention is invalid.
    backup_retention_days: int = Field(default=30, ge=0)
    trash_retention_days: int = Field(default=30, ge=0)

    backup_s3_bucket: str = ""
    backup_s3_endpoint_url: str = ""
    backup_s3_region: str = "auto"
    backup_s3_access_key: str = ""
    backup_s3_secret_key: str = ""

    app_name: str = "PrintStash"
    app_version: str = "0.12.1"

    @model_validator(mode="after")
    def validate_numeric_relationships(self) -> Settings:
        if self.max_archive_entry_mb > self.max_archive_uncompressed_mb:
            raise ValueError(
                "max_archive_entry_mb must not exceed max_archive_uncompressed_mb"
            )
        if self.sqlite_synchronous.upper() not in {"NORMAL", "FULL"}:
            raise ValueError("sqlite_synchronous must be NORMAL or FULL")
        if (
            self.s3_lifecycle_expiration_days
            and self.s3_lifecycle_transition_days
            and self.s3_lifecycle_transition_days >= self.s3_lifecycle_expiration_days
        ):
            raise ValueError(
                "s3_lifecycle_transition_days must be lower than "
                "s3_lifecycle_expiration_days"
            )
        return self

    @property
    def incoming_dir(self) -> Path:
        return self.staging_dir / "_incoming"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def max_request_bytes(self) -> int:
        return self.max_upload_bytes + MULTIPART_OVERHEAD_BYTES


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

    @property
    def max_request_bytes(self) -> int:
        return self.max_upload_bytes + MULTIPART_OVERHEAD_BYTES


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
