"""Process-wide configuration, sourced from ``VAULT_*`` environment variables.

Frozen env-only settings are wrapped by ``ConfigResolver`` which layers
runtime overrides (DB-backed) on top. See ADR-0002.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES
from pydantic import (
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

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

# Every app-owned path defaults to a fixed child of ``data_root``, so a
# deployment mounts one volume. That single mount is also what lets an import
# publish its staged file into the library by hard link: link(2) fails across
# mount points even on the same disk, and the import then copies every byte.
DATA_ROOT_LAYOUT: dict[str, str] = {
    "data_dir": "files",
    "thumb_dir": "thumbs",
    "staging_dir": "staging",
    "backup_dir": "backups",
    "artifact_cache_root": "artifact-cache",
    "embedding_cache_dir": "ai-models",
    "secrets_key_file": "db/.printstash-secrets-key",
}
SQLITE_DATABASE_PATH = "db/printstash.sqlite"


class ProcessRole(StrEnum):
    """What one process of a deployment does (``VAULT_PROCESS_ROLE``).

    ``all`` serves HTTP and runs every Job; ``api`` is the HTTP process of a
    deployment with workers; ``worker`` runs Jobs and serves nothing.
    """

    ALL = "all"
    API = "api"
    WORKER = "worker"


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

    # Declared first: the paths in ``DATA_ROOT_LAYOUT`` and ``db_url`` default
    # under it, and a field validator only sees fields declared before it.
    data_root: Path = Path("/data")

    storage_backend: str = "local"
    storage_provider: str = ""
    storage_provider_config: str = ""
    storage_provider_secrets: str = ""
    storage_identity: str = ""
    storage_provider_error: str = ""
    storage_root: str = ""
    webdav_endpoint_url: str = ""
    webdav_username: str = ""
    webdav_password: str = ""
    sftp_host: str = ""
    sftp_port: int = 22
    sftp_username: str = ""
    sftp_host_key: str = ""
    sftp_password: str = ""
    sftp_private_key_path: str = ""
    sftp_passphrase: str = ""
    storage_allow_unverified: bool = False
    data_dir: Path = Field(default=None, validate_default=True)
    thumb_dir: Path = Field(default=None, validate_default=True)
    storage_min_free_bytes: int = Field(default=1024**3, ge=0)
    storage_min_free_percent: float = Field(
        default=0, ge=0, le=100, allow_inf_nan=False
    )
    staging_dir: Path = Field(default=None, validate_default=True)
    artifact_cache_enabled: bool = False
    artifact_cache_root: Path = Field(default=None, validate_default=True)
    artifact_cache_max_bytes: int = Field(default=10 * 1024**3, ge=0)
    artifact_cache_max_entries: int = Field(default=10000, ge=0)
    artifact_cache_max_fills: int = Field(default=2, ge=1, le=64)
    artifact_cache_headroom_bytes: int = Field(default=1024**3, ge=0)
    artifact_cache_verify_every_hits: int = Field(default=100, ge=0)
    artifact_cache_fill_wait_seconds: int = Field(default=30, ge=0, le=300)

    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "auto"
    s3_addressing_style: str = Field(default="auto", pattern=r"^(auto|path|virtual)$")
    s3_access_key: str = ""
    s3_secret_key: str = ""
    # Historical installs default to the literal ``vault-data`` prefix. Typed
    # provider configuration may select another normalized namespace.
    s3_root: str = "vault-data"
    s3_presigned_url_expire_seconds: int = Field(default=900, gt=0)
    s3_multipart_threshold_mb: int = Field(default=50, gt=0)

    db_url: str = Field(default=None, validate_default=True)
    sqlite_synchronous: str = "NORMAL"
    sqlite_busy_timeout_ms: int = Field(default=30_000, ge=1)

    jwt_secret: str = DEFAULT_JWT_SECRET
    # How a fresh installation gets its first owner (resolved and validated
    # together with setup_admin_* by modules.administration.setup_policy):
    # trusted_network — a browser on the local network registers it;
    # environment — setup_admin_* creates it at startup, the browser never can;
    # disabled — no first-run path.
    setup_mode: Literal["trusted_network", "environment", "disabled"] = "disabled"
    setup_allowed_hosts: str = ""
    # The first administrator for setup_mode=environment (app-store forms,
    # unattended deployments). Consumed once while the installation has no
    # owner; it never changes an existing account. Empty values are unset:
    # install forms emit empty variables for blanks.
    setup_admin_username: str = ""
    setup_admin_password: SecretStr = SecretStr("")
    setup_admin_email: str = ""
    # Credentials persisted in the database are encrypted with this external
    # key. Empty uses a generated 0600 key file beside the SQLite database.
    secrets_key: str = ""
    secrets_key_file: Path = Field(default=None, validate_default=True)
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
    # Optional operator-supplied base URL for notification navigation links.
    public_url: str = ""

    # Keep slow file commands and engine hints off the general request limiter.
    api_command_concurrency: int = Field(default=8, ge=1, le=128)
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

    # Background work (see docs/architecture/background-work.md). The reconciler
    # tick is a safety net: hot paths and job completions nudge the one source
    # that has new work, so the interval only bounds how long a lost nudge waits.
    process_role: ProcessRole = ProcessRole.ALL
    api_runs_jobs: bool = True
    executor_id: str | None = Field(default=None, min_length=1, max_length=128)
    shared_storage: bool = False
    jobs_reconcile_interval_seconds: int = Field(default=300, ge=10, le=86400)
    jobs_reconcile_batch: int = Field(default=500, ge=1, le=10000)
    jobs_lane_headroom_factor: int = Field(default=2, ge=1, le=100)
    jobs_max_resubmits: int = Field(default=3, ge=0, le=100)
    # A subject whose Jobs of one definition already finished this many times
    # within the cooldown window waits the window out: a source that keeps
    # reporting work its Job already handled must not turn into a busy loop,
    # while a genuine re-run (a retry, a content change) still starts at once.
    jobs_resubmit_cooldown_seconds: int = Field(default=30, ge=0, le=3600)
    jobs_resubmit_burst: int = Field(default=3, ge=1, le=100)
    jobs_submit_grace_seconds: int = Field(default=60, ge=1, le=3600)
    jobs_executor_stale_seconds: int = Field(default=120, ge=10, le=86400)
    jobs_retention_days: int = Field(default=7, ge=1, le=365)
    jobs_system_retention_hours: int = Field(default=24, ge=1, le=8760)
    jobs_retention_per_user: int = Field(default=500, ge=10, le=100000)
    engine_history_retention_days: int = Field(default=7, ge=1, le=365)
    derivatives_mesh_enabled: bool = True
    derivatives_gcode_enabled: bool = True
    derivatives_toolpath_enabled: bool = True

    derivative_max_attempts: int = Field(default=5, ge=1, le=100)
    derivative_backoff_seconds: int = Field(default=30, ge=1, le=86400)
    fence_heartbeat_seconds: int = Field(default=15, ge=1, le=3600)
    fence_ttl_seconds: int = Field(default=60, ge=3, le=86400)
    # Lane concurrency. Unset ``derive_native`` follows ``max_render_jobs``.
    jobs_ingest_concurrency: int = Field(default=2, ge=1, le=64)
    jobs_derive_native_concurrency: int | None = Field(default=None, ge=1, le=64)
    jobs_derive_light_concurrency: int = Field(default=4, ge=1, le=64)
    jobs_similarity_concurrency: int = Field(default=1, ge=1, le=16)
    jobs_network_concurrency: int = Field(default=4, ge=1, le=64)
    jobs_notify_concurrency: int = Field(default=1, ge=1, le=16)
    jobs_notify_rate_per_minute: int = Field(default=30, ge=1, le=6000)
    jobs_printing_concurrency: int = Field(default=1, ge=1, le=16)
    jobs_maintenance_concurrency: int = Field(default=1, ge=1, le=16)
    # AI Search: projection and indexing, captions, and sparse expansion each
    # have their own lane, so a slow caption never holds indexing back.
    jobs_search_concurrency: int = Field(default=1, ge=1, le=16)
    jobs_captions_concurrency: int = Field(default=1, ge=1, le=16)
    jobs_expansion_concurrency: int = Field(default=1, ge=1, le=16)
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
    slow_request_ms: int = Field(default=1000, ge=1)
    # A restart request exits the API process gracefully. Enable this only when
    # Docker, systemd, Kubernetes, or another supervisor is configured to
    # relaunch it; source/dev launches stay safely disabled by default.
    restart_enabled: bool = False

    # Static ceiling on mesh density for geometry extraction + thumbnail
    # rendering. Loading + rasterising a mesh peaks (measured) at ~0.8–2 GB of RSS
    # per million triangles for STL/PLY/OBJ and ~3–4 GB/M for 3MF (its XML loader
    # is far heavier) — paid mostly inside trimesh.load_mesh and our rasteriser, so a
    # dense model can OOM-kill a library scan (issues #24/#29). Above this estimate
    # the mesh is not loaded; the file is still indexed, and 3MF still gets its
    # embedded slicer preview. This is the hard ceiling; the RAM-aware cap below
    # tightens it further on small hosts.
    mesh_max_render_triangles: int = Field(default=2_000_000, gt=0)

    # Fraction of host/cgroup RAM shared by native mesh work. Admission reserves
    # a startup floor or the estimated whole-pipeline peak for each job;
    # unknown complexity reserves the full pool. The job's admitted bytes set
    # its hard worker ceiling and loader cap. Zero disables geometry estimation,
    # while retaining containment at half of detected RAM.
    mesh_memory_budget_fraction: float = Field(default=0.5, ge=0, le=1)

    # Maximum simultaneous native mesh jobs across processes sharing this local
    # data root. This also defaults the derive.native lane. Memory reservations
    # can reduce actual concurrency when large models need more of the pool.
    # One is the safe default; zero retains serial execution compatibility.
    max_render_jobs: int = Field(default=1, ge=0)

    # Bounded prepared inputs, independent of native RAM. Zero jobs follows
    # max_render_jobs + one waiting batch (at least two). Bytes account for two
    # copies of each source, including verification pairs; I/O slots are held
    # only during materialization, never while awaiting CPU/RAM.
    mesh_prepared_max_jobs: int = Field(default=0, ge=0)
    mesh_prepared_max_mb: int = Field(default=4096, gt=0)
    mesh_source_io_jobs: int = Field(default=2, gt=0)

    # Number of faces processed per chunk in the software rasteriser. The renderer
    # builds its per-face geometry/shading arrays (each O(faces)) one chunk at a
    # time and frees them before the next, so peak render memory is O(chunk_size)
    # rather than O(total_faces) — a million-triangle mesh no longer materialises
    # ~70 MB float32 arrays all at once (#29). Lower it to shrink peak RSS further
    # on tiny containers; raise it for marginally less Python-loop overhead.
    mesh_render_face_chunk_size: int = Field(default=64_000, gt=0)

    # Width of generated Model preview images. Height keeps the renderer's 4:3
    # aspect ratio. The Settings UI offers bounded presets so higher fidelity is
    # an explicit CPU/RAM/storage tradeoff on self-hosted machines.
    model_thumbnail_width: int = Field(default=640, ge=320, le=1280)

    # Similar Models remains opt-in. Its geometry budget is bounded by the
    # renderer's adaptive budget and the descriptor implementation's own cap.
    similarity_enabled: bool = False
    similarity_fingerprint_on_ingest: bool = True
    similarity_minimum_confidence: float = Field(default=0.9, ge=0.5, le=1.0)
    similarity_triangle_cap: int = Field(
        default=MAX_ANALYSIS_FACES, ge=100, le=MAX_ANALYSIS_FACES
    )
    similarity_sample_points: int = Field(default=5000, ge=256, le=5000)
    similarity_max_candidates: int = Field(default=20, ge=1, le=100)
    similarity_page_size: int = Field(default=100, ge=1, le=1000)
    similarity_schedule_hours: int = Field(default=0, ge=0, le=168)
    similarity_embeddings_enabled: bool = False
    embedding_provider: Literal["onnx_cpu", "openai_compatible"] = "onnx_cpu"
    embedding_local_enabled: bool = False
    embedding_local_model_dir: str = ""
    embedding_model_key: str = ""
    embedding_onnx_threads: int = Field(default=1, ge=1, le=4)
    # Process-shared residency is separate from temporary geometric allocations.
    # Restart all processes together when changing either memory partition.
    embedding_memory_budget_fraction: float = Field(default=0.25, gt=0, lt=1)
    embedding_resident_workers: int = Field(default=1, ge=1, le=2)
    embedding_worker_memory_mb: int = Field(default=1024, ge=512)
    embedding_batch_size: int = Field(default=8, ge=1, le=8)
    embedding_cache_dir: Path = Field(default=None, validate_default=True)
    embedding_cache_max_bytes: int = Field(
        default=4294967296, ge=1048576, le=1099511627776
    )
    embedding_download_enabled: bool = False
    embedding_mirror_url: str = ""
    embedding_endpoint: str = ""
    embedding_model: str = ""
    embedding_revision: str = "configured-v1"
    embedding_model_repo: str | None = None
    embedding_native_dimension: int = Field(default=384, ge=1, le=4096)
    embedding_api_key: SecretStr = SecretStr("")
    embedding_headers: dict[str, SecretStr] = Field(default_factory=dict)
    embedding_timeout_seconds: float = Field(default=15, gt=0, le=120)
    embedding_max_input_characters: int = Field(default=16384, ge=128, le=16384)
    chat_endpoint: str = ""
    chat_model: str = ""
    chat_revision: str = "configured-v1"
    chat_api_key: SecretStr = SecretStr("")
    chat_headers: dict[str, SecretStr] = Field(default_factory=dict)
    chat_timeout_seconds: float = Field(default=15, gt=0, le=120)
    chat_prefer_responses: bool = False
    chat_supports_images: bool = False
    search_native_vectors_enabled: bool = False
    ai_search_enabled: bool = False
    ai_search_lexical_backend: Literal["auto", "ranked_like"] = "auto"
    ai_search_captions_enabled: bool = False
    ai_search_nl_filters_enabled: bool = False
    ai_search_send_rendered_images: bool = False
    ai_search_send_query_images: bool = False
    ai_search_query_timeout_seconds: float = Field(default=3, ge=0.05, le=30)
    ai_search_semantic_floor: float = Field(default=0.35, ge=-1, le=1)
    ai_search_lexical_weight: float = Field(default=1, gt=0, le=10)
    ai_search_semantic_weight: float = Field(default=1, gt=0, le=10)
    ai_search_rrf_k: int = Field(default=60, ge=1, le=1000)

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
    # Toolpath conversion runs in a bounded disposable official libbgcode process.
    bgcode_executable: str = "bgcode"
    toolpath_input_max_mb: int = Field(default=128, ge=1)
    toolpath_output_max_mb: int = Field(default=32, ge=1)
    toolpath_timeout_seconds: int = Field(default=30, ge=1)
    toolpath_memory_max_mb: int = Field(default=512, ge=64)

    mesh_step_timeout_seconds: int = Field(default=90, gt=0)

    # Every mesh derivative (geometry, thumbnail, fingerprint) runs in a
    # disposable child so a file that outgrows its memory budget costs one
    # process rather than the API. This is that child's wall-clock deadline.
    mesh_worker_timeout_seconds: int = Field(default=300, ge=10, le=3600)

    # Oversized STL previews run in a disposable, streaming worker. The worker
    # deadline is intentionally capped by the service so an operator override
    # cannot leave an ingestion thread waiting indefinitely.
    mesh_stream_timeout_seconds: int = Field(default=45, gt=0, le=45)

    # Optional static bearer token guarding the Prometheus /metrics endpoint.
    # Empty = open on the trusted internal network (see docs/known-limitations).
    metrics_token: str = ""

    # URL + ZIP import (see modules/ingestion/importer.py).
    url_import_max_redirects: int = Field(default=5, ge=0)
    # Disposable expanded outputs per batch; source archives retain separate caps.
    ingestion_batch_max_files: int = Field(default=4, gt=0)
    ingestion_batch_max_mb: int = Field(default=512, gt=0)
    max_archive_entries: int = Field(default=500, gt=0)
    max_archive_entry_mb: int = Field(default=512, gt=0)
    max_archive_uncompressed_mb: int = Field(default=2048, gt=0)
    max_archive_central_directory_mb: int = Field(default=32, gt=0)
    max_archive_depth: int = Field(default=32, gt=0)
    max_archive_path_bytes: int = Field(default=1024, gt=0)

    # Deprecated compatibility input. MakerWorld files are transferred by the
    # browser extension and this value is never used for network requests.
    makerworld_cookie: str = ""

    backup_dir: Path = Field(default=None, validate_default=True)
    # Zero means eligible for cleanup immediately; negative retention is invalid.
    backup_retention_days: int = Field(default=30, ge=0)
    trash_retention_days: int = Field(default=30, ge=0)
    # Approved GC plans retain restorable catalog rows for this interval before
    # any exact owned object may be physically deleted.
    gc_quarantine_days: int = Field(default=7, ge=1, le=90)

    backup_s3_bucket: str = ""
    backup_s3_endpoint_url: str = ""
    backup_s3_region: str = "auto"
    backup_s3_access_key: str = ""
    backup_s3_secret_key: str = ""

    app_name: str = "PrintStash"
    app_version: str = "0.14.0"

    @field_validator(*DATA_ROOT_LAYOUT, "db_url", mode="before")
    @classmethod
    def default_under_data_root(cls, value: object, info: ValidationInfo) -> object:
        # An empty override (``VAULT_DATA_DIR=``, as an unset Compose variable
        # renders) means "use the layout", never ``Path("")``, the working dir.
        if value is not None and value != "":
            return value
        root = info.data.get("data_root")
        if not isinstance(root, Path):
            # data_root failed its own validation, which already reports it.
            return value
        if info.field_name == "db_url":
            return f"sqlite:///{root / SQLITE_DATABASE_PATH}"
        return root / DATA_ROOT_LAYOUT[str(info.field_name)]

    @model_validator(mode="after")
    def validate_numeric_relationships(self) -> Settings:
        if self.max_archive_entry_mb > self.max_archive_uncompressed_mb:
            raise ValueError(
                "max_archive_entry_mb must not exceed max_archive_uncompressed_mb"
            )
        if self.sqlite_synchronous.upper() not in {"NORMAL", "FULL"}:
            raise ValueError("sqlite_synchronous must be NORMAL or FULL")
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
    def frozen(self) -> Settings:
        """The environment-time settings, before any runtime override."""
        return self._frozen

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
    from sqlalchemy.engine.url import make_url

    database = make_url(db_url).database
    if not database or database == ":memory:":
        return None
    return Path(database)


def ensure_dirs(*, create_managed_roots: bool = False) -> None:
    """Create app-owned directories, optionally provisioning local roots.

    Runtime configuration and normal startup may create staging, inbox,
    backups, and the SQLite parent.  Managed data/thumb roots are mount points
    and must already exist and be enrolled; only the first-run setup flow may
    opt into creating them explicitly.
    """
    settings.staging_dir.mkdir(parents=True, exist_ok=True)
    settings.incoming_dir.mkdir(parents=True, exist_ok=True)
    settings.backup_dir.mkdir(parents=True, exist_ok=True)

    if create_managed_roots and settings.storage_backend == "local":
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        settings.thumb_dir.mkdir(parents=True, exist_ok=True)

    db_path = _sqlite_db_path(settings.db_url)
    if db_path is not None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
