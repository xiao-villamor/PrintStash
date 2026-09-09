"""Storage capabilities, object identity and create-only publication contracts."""

from __future__ import annotations

import os
import secrets
import tempfile
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Iterator

from app.core.logging import get_logger
from app.modules.storage.delivery_contracts import BrowserDownload
from app.modules.storage.filesystem import FsKind
from app.modules.storage.storage_identity import StorageTargetIdentity

logger = get_logger(__name__)


_DIRECT_ADAPTER_IDENTITY = secrets.token_hex(32)


class ObjectIdentity(StrEnum):
    """How a creation receipt binds to the exact bytes it describes."""

    INODE = "inode"
    VERSION = "version"
    ETAG = "etag"
    NONE = "none"


class StorageTier(StrEnum):
    """The strongest write/delete guarantee a bound storage adapter provides."""

    VERIFIED = "verified"
    GUARDED = "guarded"
    UNGUARDED = "unguarded"


class CapacityReliability(StrEnum):
    """How strongly an adapter can stand behind one capacity observation."""

    EXACT = "exact"
    ESTIMATED = "estimated"


@dataclass(frozen=True)
class StorageCapacity:
    """Optional, credential-free capacity evidence for one configured namespace.

    ``None`` from :meth:`StorageBackend.capacity` means unsupported/unknown. It
    must never be translated into zero bytes of capacity.
    """

    total_bytes: int | None
    used_bytes: int | None
    available_bytes: int | None
    quota_bytes: int | None
    measured_at: datetime
    method: str
    reliability: CapacityReliability

    def __post_init__(self) -> None:
        values = (
            self.total_bytes,
            self.used_bytes,
            self.available_bytes,
            self.quota_bytes,
        )
        if any(value is not None and value < 0 for value in values):
            raise ValueError("capacity bytes must be nonnegative")
        if not self.method:
            raise ValueError("capacity method is required")


@dataclass(frozen=True)
class StorageCapabilities:
    """Capabilities measured for one configured storage adapter."""

    conditional_create: bool
    object_identity: ObjectIdentity
    verified_delete: bool
    conditional_replace: bool
    namespace_ownership: bool
    direct_path: bool
    browser_multipart_upload: bool = False
    multipart_sha256_checksums: bool = False

    @property
    def tier(self) -> StorageTier:
        if not self.conditional_create:
            return StorageTier.UNGUARDED
        if self.verified_delete and self.conditional_replace:
            return StorageTier.VERIFIED
        return StorageTier.GUARDED

    @property
    def warnings(self) -> tuple[str, ...]:
        warnings: list[str] = []
        if not self.conditional_create:
            warnings.append(
                "Two simultaneous uploads of the same revision can silently "
                "overwrite each other."
            )
        if self.object_identity is ObjectIdentity.NONE:
            warnings.append("PrintStash cannot verify that a file is the one it wrote.")
        if not self.verified_delete:
            warnings.append(
                "Interrupted uploads can leave retained bytes requiring storage-specific cleanup."
            )
        if not self.conditional_replace:
            warnings.append(
                "PrintStash cannot conditionally replace an object while its proof "
                "still matches."
            )
        if not self.namespace_ownership:
            warnings.append(
                "PrintStash cannot confirm that a file is inside its owned storage root."
            )
        return tuple(warnings)

    def as_dict(self) -> dict[str, object]:
        return {
            "conditional_create": self.conditional_create,
            "object_identity": self.object_identity.value,
            "verified_delete": self.verified_delete,
            "conditional_replace": self.conditional_replace,
            "namespace_ownership": self.namespace_ownership,
            "direct_path": self.direct_path,
            "browser_multipart_upload": self.browser_multipart_upload,
            "multipart_sha256_checksums": self.multipart_sha256_checksums,
            "tier": self.tier.value,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class LocalRootProbe:
    role: str
    path: str
    fs_kind: FsKind
    hardlink: bool
    exclusive_create: bool
    directory_fsync: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "path": self.path,
            "fs_kind": self.fs_kind,
            "hardlink": self.hardlink,
            "exclusive_create": self.exclusive_create,
            "directory_fsync": self.directory_fsync,
        }


@dataclass(frozen=True)
class StorageObjectInfo:
    size: int
    etag: str | None = None
    version_id: str | None = None
    modified_at: datetime | None = None


class StorageCollisionError(FileExistsError):
    """A create-only write found an object already present at its exact key."""


class StorageConfigurationError(RuntimeError):
    """The selected storage target is missing or cannot be accessed safely."""


@dataclass(frozen=True)
class CreationReceipt:
    """Positive evidence that one storage operation created one exact object.

    The local fingerprint prevents rollback cleanup from unlinking a file that
    replaced our object after creation. Remote stores use a per-operation token
    written into object metadata for the same purpose.
    """

    key: str
    size: int
    token: str
    backend: str
    namespace: str
    etag: str | None = None
    version_id: str | None = None
    device: int | None = None
    inode: int | None = None
    ctime_ns: int | None = None
    # Credential-free configured-destination identity. Older serialized
    # receipts omit it and are accepted only by explicitly compatible local
    # recovery paths; remote recovery fails closed.
    provider_ref: str | None = None


@dataclass(frozen=True)
class NativeMultipartCapability:
    """Provider-neutral guarantees required for direct browser parts."""

    part_size: int
    max_parts: int
    checksum_algorithm: str = "sha256"


@dataclass(frozen=True)
class NativeMultipartHandle:
    """Private operation identity; callers must persist this encrypted."""

    key: str
    upload_id: str
    ownership_token: str


@dataclass(frozen=True)
class NativeMultipartPart:
    part_number: int
    size_bytes: int
    checksum_sha256: str
    etag: str


class StorageBackend(ABC):
    """Abstract interface for vault file operations.

    Keys are opaque identifiers: for the local backend they are absolute
    filesystem paths; for S3 they are object keys within the bucket.

    Callers must never branch on the concrete backend type. Anything that
    needs a real filesystem path uses ``local_path()``; anything moving a
    staged upload into the vault uses ``move_in()``; HTTP handlers deciding
    between file and streaming responses use ``direct_path()``.
    """

    backend_name: str
    # Stable manifest identity.  Concrete adapters may provide a provider
    # flavour (for example ``cloudflare_r2``) while retaining a transport
    # (``s3``); legacy fakes fall back to ``backend_name``.
    provider_id: str
    transport: str

    @property
    def storage_target(self) -> StorageTargetIdentity | None:
        """Versioned target identity, independent of locator/ownership hashes."""
        return None

    @property
    def capabilities(self) -> StorageCapabilities:
        """Return guarantees measured for this configured adapter."""
        return getattr(
            self,
            "_capabilities",
            StorageCapabilities(
                conditional_create=False,
                object_identity=ObjectIdentity.NONE,
                verified_delete=False,
                conditional_replace=False,
                namespace_ownership=False,
                direct_path=False,
            ),
        )

    @property
    def probe_diagnostics(self) -> dict[str, object]:
        return getattr(self, "_probe_diagnostics", {})

    def destructive_lifecycle_findings(self) -> list[dict[str, object]]:
        """Read-only operator policy findings that may expire managed bytes."""
        return []

    @property
    def native_multipart_capability(self) -> NativeMultipartCapability | None:
        """Return browser-upload guarantees, or ``None`` for API fallback."""

        return None

    def begin_native_multipart(
        self,
        *,
        session_id: str,
        filename: str,
        media_type: str,
    ) -> NativeMultipartHandle:
        del session_id, filename, media_type
        raise NotImplementedError("native_multipart_not_supported")

    def sign_native_multipart_part(
        self,
        handle: NativeMultipartHandle,
        *,
        part_number: int,
        checksum_sha256: str,
        expires_seconds: int,
    ) -> str:
        del handle, part_number, checksum_sha256, expires_seconds
        raise NotImplementedError("native_multipart_not_supported")

    def list_native_multipart_parts(
        self, handle: NativeMultipartHandle
    ) -> list[NativeMultipartPart]:
        del handle
        raise NotImplementedError("native_multipart_not_supported")

    def complete_native_multipart(
        self,
        handle: NativeMultipartHandle,
        parts: list[NativeMultipartPart],
    ) -> CreationReceipt:
        del handle, parts
        raise NotImplementedError("native_multipart_not_supported")

    def abort_native_multipart(self, handle: NativeMultipartHandle) -> None:
        del handle
        raise NotImplementedError("native_multipart_not_supported")

    def recover_native_multipart_completion(
        self,
        handle: NativeMultipartHandle,
        *,
        expected_size: int,
        expected_sha256: str | None,
    ) -> CreationReceipt | None:
        del handle, expected_size, expected_sha256
        return None

    def namespace_for(self, key: str) -> str:
        """Return the owned namespace that contains an opaque storage key."""
        del key
        raise NotImplementedError("storage_namespace_not_supported")

    def validate_restore_key(self, key: str) -> None:
        """Validate that a restore destination belongs to this backend."""
        self.namespace_for(key)

    def reclaim_unverified(
        self,
        key: str,
        *,
        expected_size: int,
        expected_etag: str | None,
        expected_sha256: str | None = None,
        expected_version_id: str | None = None,
    ) -> bool:
        """Best-effort delete after a ledger-owned caller rechecks evidence."""
        del key, expected_size, expected_etag, expected_sha256, expected_version_id
        return False

    @abstractmethod
    def blob_key(self, slug: str, version: int, filename: str) -> str: ...

    @abstractmethod
    def thumbnail_key(self, file_id: int) -> str: ...

    def thumbnail_variant_key(
        self, file_id: int, source_sha256: str, recipe_fingerprint: str
    ) -> str:
        """Create-only key for an immutable thumbnail generation.

        The base key remains the legacy compatibility address. Keeping this
        derivation on the opaque-key adapter avoids callers guessing whether a
        configured backend uses filesystem paths, S3 keys, or another namespace.
        """
        base = self.thumbnail_key(file_id)
        suffix = ".webp"
        stem = base[: -len(suffix)] if base.endswith(suffix) else base
        return f"{stem}-{source_sha256[:12]}-{recipe_fingerprint[:16]}.webp"

    @abstractmethod
    def source_cover_key(self, provenance_source_id: int) -> str: ...

    @abstractmethod
    def capture_upload_slot_key(self, slot_id: str) -> str: ...

    @abstractmethod
    def legacy_thumbnail_key(self, file_id: int) -> str:
        """PNG key used before thumbnails moved to WebP. Read/delete only —
        new thumbnails are always written under ``thumbnail_key``."""

    @abstractmethod
    def stl_cache_key(self, sha256: str) -> str:
        """Key for a derived-STL preview cached by source sha256."""

    @abstractmethod
    def collection_image_key(self, collection_id: int, name: str) -> str:
        """Key for an image embedded in a collection's readme. ``name`` is a
        server-generated ``{sha256}.{ext}`` — never raw user input."""

    @abstractmethod
    def document_file_key(self, document_id: int, name: str) -> str:
        """Key for a Document's binary blob (PDF/other). ``name`` is a sanitised
        filename — never raw user input."""

    @abstractmethod
    def document_image_key(self, document_id: int, name: str) -> str:
        """Key for an image embedded in a markdown Document. ``name`` is a
        server-generated ``{sha256}.{ext}`` — never raw user input."""

    @abstractmethod
    def multipart_model_cover_key(self, multipart_model_id: int, name: str) -> str:
        """Key for a normalized Multipart Model cover uploaded by its user."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    def write_stream(self, src: BinaryIO, key: str) -> int:
        """Compatibility create-only write; callers needing proof use create_*()."""
        return self.create_stream(src, key).size

    def write_bytes(self, data: bytes, key: str) -> int:
        """Compatibility create-only write; never replaces an existing key."""
        return self.create_bytes(data, key).size

    def create_stream(self, src: BinaryIO, key: str) -> CreationReceipt:
        """Create *key* without replacement.

        Adapters must provide a backend-native atomic conditional create. A
        check-then-upload compatibility fallback would silently reintroduce the
        overwrite race this contract exists to prevent.
        """
        del src, key
        raise NotImplementedError("atomic_create_not_supported")

    def create_bytes(self, data: bytes, key: str) -> CreationReceipt:
        from io import BytesIO

        return self.create_stream(BytesIO(data), key)

    def replace_stream(
        self, src: BinaryIO, receipt: CreationReceipt
    ) -> CreationReceipt:
        """Atomically replace an object only while positive proof still matches."""
        del src, receipt
        raise NotImplementedError("atomic_replace_not_supported")

    def replace_bytes(self, data: bytes, receipt: CreationReceipt) -> CreationReceipt:
        from io import BytesIO

        return self.replace_stream(BytesIO(data), receipt)

    def rollback_create(self, receipt: CreationReceipt) -> bool:
        """Remove a just-created object only when its receipt still matches.

        Compatibility adapters cannot positively verify their random token, so
        they fail closed and leak the uncertain object.
        """
        del receipt
        return False

    def creation_matches(self, receipt: CreationReceipt) -> bool:
        """Return whether the exact object still matches positive proof."""
        del receipt
        return False

    def adopt_existing(
        self, key: str, *, expected_size: int, expected_sha256: str
    ) -> CreationReceipt:
        """Create proof for a legacy object whose immutable content is known.

        Backends must fail closed unless they can bind the verified content to
        an identity that later deletion can compare atomically. This is a
        compatibility seam for pre-ledger Artifacts, not a generic claim API.
        """
        del key, expected_size, expected_sha256
        raise NotImplementedError("existing_storage_adoption_not_supported")

    def verify_destructive_access(self, keys: list[str]) -> None:
        """Prove delete capability without touching any pre-existing object."""
        del keys
        raise NotImplementedError("destructive_access_probe_not_supported")

    @abstractmethod
    def move(self, src_key: str, dest_key: str) -> None: ...

    @abstractmethod
    def stat_size(self, key: str) -> int: ...

    def object_info(self, key: str) -> StorageObjectInfo | None:
        """Return existence, size, and a cache validator through one seam."""
        if not self.exists(key):
            return None
        return StorageObjectInfo(size=self.stat_size(key))

    @abstractmethod
    def read_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def stream_chunks(
        self, key: str, chunk_size: int = 1024 * 1024
    ) -> Iterator[bytes]: ...

    @abstractmethod
    def download_to_path(self, key: str, dest: Path) -> Path: ...

    @abstractmethod
    def upload_file(self, src: Path, key: str) -> None: ...

    @abstractmethod
    def ensure_setup(self) -> None: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    @abstractmethod
    def list_keys(self, prefix: str = "") -> list[str]: ...

    def list_prefix(self, prefix: str = "") -> list[str]:
        """List the objects below *prefix* through the canonical list seam."""
        return self.list_keys(prefix)

    @abstractmethod
    def walk_keys(self, prefix: str = "") -> Iterator[str]: ...

    @abstractmethod
    def usage(self, prefix: str = "") -> dict: ...

    def capacity(self) -> StorageCapacity | None:
        """Return bounded provider capacity evidence, or ``None`` if unknown.

        Implementations must not emulate capacity by walking an unbounded
        namespace. Enumeration belongs to explicit background inventory/audit.
        """
        return None

    def delivery_diagnostics(self) -> dict:
        return {
            "mode": "proxy",
            "native_candidate": False,
            "ranges": self.supports_ranges,
        }

    def browser_download(
        self,
        key: str,
        filename: str,
        media_type: str,
        *,
        origin: str | None = None,
        inline: bool = False,
    ) -> BrowserDownload | None:
        """Return a short-lived, header-free object capability when supported."""
        return None

    @property
    def supports_ranges(self) -> bool:
        return False

    def stream_range(self, key: str, start: int, end: int) -> Iterator[bytes]:
        raise NotImplementedError("storage_range_unavailable")

    @abstractmethod
    def health_probe(self) -> dict: ...

    @abstractmethod
    def direct_path(self, key: str) -> Path | None:
        """Return the on-disk path for *key*, or None when the backend has
        no direct filesystem representation (S3)."""
        ...

    @contextmanager
    def local_path(self, key: str) -> Iterator[Path]:
        """Yield a local filesystem path for *key*.

        Local backend yields the real path. Remote backends download to a
        temporary file and remove it on exit. The single owner of the
        temp-file lifecycle — callers never manage cleanup.
        """
        direct = self.direct_path(key)
        if direct is not None:
            yield direct
            return
        fd, name = tempfile.mkstemp(suffix=Path(key).suffix)
        os.close(fd)
        tmp = Path(name)
        tmp.unlink()
        try:
            self.download_to_path(key, tmp)
            yield tmp
        finally:
            tmp.unlink(missing_ok=True)

    def move_in(self, src: Path, dest_key: str) -> CreationReceipt:
        """Move a local staged file into the vault at *dest_key*.

        Concrete local storage overrides this with create-only placement;
        remote backends upload and then remove the staged file.
        """
        with src.open("rb") as incoming:
            receipt = self.create_stream(incoming, dest_key)
        try:
            src.unlink()
        except OSError:
            # Destination publication already succeeded. Returning its receipt
            # lets the caller commit ownership (or roll it back precisely);
            # failing here would strand an untracked destination. A duplicate
            # staging file is the data-preserving failure mode.
            logger.warning(
                "storage move-in left staged source after successful create",
                extra={"source": str(src), "destination": dest_key},
            )
        return receipt


# ---------------------------------------------------------------------------
# Local filesystem backend
# ---------------------------------------------------------------------------


class UnavailableStorageBackend(StorageBackend):
    """Fail-closed backend used when selected provider configuration is invalid."""

    backend_name = "unavailable"
    provider_id = "unavailable"
    transport = "unavailable"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        self._capabilities = StorageCapabilities(
            False, ObjectIdentity.NONE, False, False, False, False
        )
        self._probe_diagnostics = {"available": False, "error": reason}

    def _fail(self):
        raise StorageConfigurationError(f"storage_unavailable:{self.reason}")

    def blob_key(self, slug: str, version: int, filename: str) -> str:
        del slug, version, filename
        return self._fail()

    def thumbnail_key(self, file_id: int) -> str:
        del file_id
        return self._fail()

    def source_cover_key(self, provenance_source_id: int) -> str:
        del provenance_source_id
        return self._fail()

    def capture_upload_slot_key(self, slot_id: str) -> str:
        del slot_id
        return self._fail()

    def legacy_thumbnail_key(self, file_id: int) -> str:
        del file_id
        return self._fail()

    def stl_cache_key(self, sha256: str) -> str:
        del sha256
        return self._fail()

    def collection_image_key(self, collection_id: int, name: str) -> str:
        del collection_id, name
        return self._fail()

    def document_file_key(self, document_id: int, name: str) -> str:
        del document_id, name
        return self._fail()

    def document_image_key(self, document_id: int, name: str) -> str:
        del document_id, name
        return self._fail()

    def multipart_model_cover_key(self, multipart_model_id: int, name: str) -> str:
        del multipart_model_id, name
        return self._fail()

    def exists(self, key: str) -> bool:
        del key
        return self._fail()

    def create_stream(self, src: BinaryIO, key: str) -> CreationReceipt:
        del src, key
        return self._fail()

    def replace_stream(
        self, src: BinaryIO, receipt: CreationReceipt
    ) -> CreationReceipt:
        del src, receipt
        return self._fail()

    def move(self, src_key: str, dest_key: str) -> None:
        del src_key, dest_key
        self._fail()

    def stat_size(self, key: str) -> int:
        del key
        return self._fail()

    def read_bytes(self, key: str) -> bytes:
        del key
        return self._fail()

    def stream_chunks(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        del key, chunk_size
        self._fail()
        yield b""

    def download_to_path(self, key: str, dest: Path) -> Path:
        del key, dest
        return self._fail()

    def upload_file(self, src: Path, key: str) -> None:
        del src, key
        self._fail()

    def ensure_setup(self) -> None:
        return None

    def delete(self, key: str) -> None:
        del key
        self._fail()

    def list_keys(self, prefix: str = "") -> list[str]:
        del prefix
        return self._fail()

    def walk_keys(self, prefix: str = "") -> Iterator[str]:
        del prefix
        self._fail()
        yield ""

    def usage(self, prefix: str = "") -> dict:
        del prefix
        return self._fail()

    def browser_download(
        self,
        key: str,
        filename: str,
        media_type: str,
        *,
        origin: str | None = None,
        inline: bool = False,
    ) -> BrowserDownload | None:
        return self._fail()

    def health_probe(self) -> dict:
        return {
            "backend": self.backend_name,
            "ok": False,
            "error": self.reason,
            "capabilities": self.capabilities.as_dict(),
            "diagnostics": self.probe_diagnostics,
        }

    def direct_path(self, key: str) -> Path | None:
        del key
        return None
