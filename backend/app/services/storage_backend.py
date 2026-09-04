"""Storage backend abstraction: local filesystem and S3-compatible stores."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import stat as stat_module
import tempfile
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Iterator

from app.core.config import settings
from app.core.logging import get_logger
from app.services.filesystem import FsKind, detect_fs_kind

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


@dataclass(frozen=True)
class StorageCapabilities:
    """Capabilities measured for one configured storage adapter."""

    conditional_create: bool
    object_identity: ObjectIdentity
    verified_delete: bool
    conditional_replace: bool
    namespace_ownership: bool
    direct_path: bool

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
                "Interrupted uploads can leave files for the orphan sweep to reclaim."
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


def _fsync_directory(path: Path) -> None:
    """Persist directory-entry changes after create/rename/unlink."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _copy_stream_create_only(src: BinaryIO, dest: Path) -> Path:
    """Fully stage and fsync a stream, then publish *dest* atomically/no-replace."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".printstash-download-", dir=dest.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as destination:
            shutil.copyfileobj(src, destination)
            destination.flush()
            os.fsync(destination.fileno())
        try:
            os.link(temp, dest, follow_symlinks=False)
        except FileExistsError as exc:
            raise StorageCollisionError(str(dest)) from exc
        _fsync_directory(dest.parent)
        return dest
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            logger.warning(
                "storage download temp cleanup failed", extra={"path": str(temp)}
            )


@dataclass(frozen=True)
class StorageObjectInfo:
    size: int
    etag: str | None = None
    version_id: str | None = None


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

    @abstractmethod
    def presigned_download_url(self, key: str, filename: str) -> str | None: ...

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

    def presigned_download_url(self, key: str, filename: str) -> str | None:
        del key, filename
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


class LocalStorageBackend(StorageBackend):
    backend_name = "local"
    provider_id = "local"
    transport = "local"
    _BINDING_FILENAME = ".printstash-storage-root.json"
    _BINDING_FORMAT = 1

    def __init__(
        self,
        *,
        external_roots: tuple[Path, ...] = (),
        external_root_bindings: dict[Path, dict[str, object]] | None = None,
    ) -> None:
        self._capabilities = StorageCapabilities(
            conditional_create=True,
            object_identity=ObjectIdentity.INODE,
            verified_delete=True,
            conditional_replace=True,
            namespace_ownership=True,
            direct_path=True,
        )
        self._probe_diagnostics: dict[str, object] = {"probed": False}
        self._external_roots = tuple(
            Path(root).expanduser().resolve(strict=False) for root in external_roots
        )
        self._external_root_bindings = {
            Path(root).expanduser().resolve(strict=False): payload
            for root, payload in (external_root_bindings or {}).items()
        }
        self._roots_ready = True
        self.recovery_mode = False
        self._startup_checked = False
        self._root_binding_diagnostics: dict[str, object] = {}

    @staticmethod
    def _installation_identity() -> str:
        configured = str(getattr(settings, "storage_identity", "") or "").strip()
        if len(configured) == 64 and all(
            char in "0123456789abcdefABCDEF" for char in configured
        ):
            return configured.lower()
        # Direct adapter users (migration tooling and isolated tests) have no
        # SystemConfig session to persist through. Keep a random process-local
        # identity there; production composition persists a valid
        # ``storage_identity`` before constructing the backend.  An invalid
        # configured value deliberately falls back to a different identity so
        # existing sentinels fail closed instead of binding to malformed data.
        return _DIRECT_ADAPTER_IDENTITY

    def _bind_root(self, role: str, root: Path) -> bool:
        """Validate the durable marker for an existing managed root.

        Marker creation is deliberately separate: startup must never turn a
        missing mount into an empty shadow directory.  Legacy adoption uses
        :func:`enroll_legacy_local_root` with explicit DB evidence (or an
        administrator's explicit recovery confirmation).
        """
        marker = root / self._BINDING_FILENAME
        expected = {
            "format": self._BINDING_FORMAT,
            "installation": self._installation_identity(),
            "role": role,
        }
        try:
            raw = marker.read_text(encoding="utf-8")
            actual = json.loads(raw)
            # Markerless and pre-format roots are handled only by the explicit
            # legacy enrollment path, which proves DB-referenced bytes before
            # writing a format-1 binding. Mutation paths never auto-adopt them.
            if (
                not isinstance(actual, dict)
                or type(actual.get("format")) is not int
                or actual != expected
            ):
                self._root_binding_diagnostics[role] = "binding_mismatch"
                return False
            return True
        except FileNotFoundError:
            self._root_binding_diagnostics[role] = "binding_missing"
            return False
        except (OSError, ValueError, TypeError):
            self._root_binding_diagnostics[role] = "binding_invalid"
            return False

    @staticmethod
    def _probe_root(role: str, root: Path) -> LocalRootProbe:
        fd, source_name = tempfile.mkstemp(
            prefix=".printstash-hardlink-probe-", dir=root
        )
        os.close(fd)
        source = Path(source_name)
        target = source.with_name(f"{source.name}.link")
        hardlink = False
        try:
            os.link(source, target, follow_symlinks=False)
            hardlink = True
        except OSError:
            pass
        finally:
            target.unlink(missing_ok=True)
            source.unlink(missing_ok=True)

        exclusive_create = False
        probe = root / f".printstash-exclusive-probe-{uuid.uuid4().hex}"
        try:
            fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            exclusive_create = True
        except OSError:
            pass
        finally:
            probe.unlink(missing_ok=True)

        directory_fsync = True
        try:
            _fsync_directory(root)
        except OSError:
            directory_fsync = False
        return LocalRootProbe(
            role=role,
            path=str(root),
            fs_kind=detect_fs_kind(root),
            hardlink=hardlink,
            exclusive_create=exclusive_create,
            directory_fsync=directory_fsync,
        )

    def _assert_no_managed_escape(self, path: Path) -> None:
        """Reject a key lexically inside a managed root that resolves outside it."""
        lexical = path.expanduser().absolute()
        roots = [settings.data_dir, settings.thumb_dir, *self._external_roots]
        backup_root = getattr(settings, "backup_dir", None)
        if backup_root is not None:
            roots.append(backup_root)
        for configured_root in roots:
            lexical_root = Path(configured_root).expanduser().absolute()
            if lexical == lexical_root or lexical.is_relative_to(lexical_root):
                if not lexical_root.is_dir():
                    # A missing bind mount must never be replaced by a directory
                    # created by the application.  Otherwise a container restart
                    # can successfully write into its own writable layer while
                    # the real vault is still unmounted.
                    raise StorageConfigurationError("storage_root_unavailable")
                resolved_root = lexical_root.resolve(strict=False)
                resolved = lexical.resolve(strict=False)
                if resolved != resolved_root and not resolved.is_relative_to(
                    resolved_root
                ):
                    raise StorageCollisionError("managed_storage_symlink_escape")
                return

    def _assert_root_binding_for(self, path: Path, *, mutation: bool = True) -> None:
        """Revalidate the configured root immediately before a local mutation.

        Startup probes are only a snapshot. A mount can disappear or its
        sentinel can be replaced while the process is running, so every
        mutating operation must recheck the binding before creating a parent,
        opening a destination, or quarantining an object.
        """
        lexical = path.expanduser().absolute()
        roots = (("data", Path(settings.data_dir)), ("thumb", Path(settings.thumb_dir)))
        for role, configured_root in roots:
            root = configured_root.expanduser().absolute()
            if lexical != root and not lexical.is_relative_to(root):
                continue
            if not root.is_dir():
                raise StorageConfigurationError("storage_root_unavailable")
            resolved_root = root.resolve(strict=False)
            resolved = lexical.resolve(strict=False)
            if resolved != resolved_root and not resolved.is_relative_to(resolved_root):
                raise StorageCollisionError("managed_storage_symlink_escape")
            if mutation and not self._bind_root(role, root):
                raise StorageConfigurationError("storage_root_unavailable")
            return

    def _open_pinned_parent(self, path: Path) -> tuple[int, int, str, Path, str] | None:
        """Open a managed destination through a pinned root directory fd.

        Absolute path operations can silently switch to a replacement mount
        after validation.  Walking descendants with ``*at`` operations keeps a
        publication on the root that was validated, and the caller compares the
        root identity again before reporting success.
        """
        lexical = path.expanduser().absolute()
        roots = [
            ("data", Path(settings.data_dir)),
            ("thumb", Path(settings.thumb_dir)),
            *(("external", root) for root in self._external_roots),
        ]
        for role, configured_root in roots:
            root = configured_root.expanduser().absolute()
            if lexical == root or not lexical.is_relative_to(root):
                continue
            # This check must precede opening/creating any descendant.  The
            # descriptor below then pins the validated root for the rest of
            # the publication, even if the pathname is concurrently remounted.
            if role == "external":
                if not root.is_dir():
                    raise StorageConfigurationError("storage_root_unavailable")
                resolved = lexical.resolve(strict=False)
                if resolved != root.resolve(
                    strict=False
                ) and not resolved.is_relative_to(root.resolve(strict=False)):
                    raise StorageCollisionError("managed_storage_symlink_escape")
            else:
                self._assert_root_binding_for(lexical)
            root_path_stat = os.stat(root, follow_symlinks=False)
            root_fd = os.open(
                root,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
            )
            parent_fd = os.dup(root_fd)
            try:
                if role == "external":
                    self._assert_external_binding_pinned(root_fd, root)
                relative = lexical.relative_to(root)
                parts = relative.parts
                if not parts:
                    raise StorageConfigurationError("storage_destination_invalid")
                for part in parts[:-1]:
                    if role == "external":
                        if not root.is_dir():
                            raise StorageConfigurationError("storage_root_unavailable")
                    else:
                        self._assert_root_binding_for(lexical)
                    try:
                        os.mkdir(part, mode=0o755, dir_fd=parent_fd)
                    except FileExistsError:
                        pass
                    child_fd = os.open(
                        part,
                        os.O_RDONLY
                        | getattr(os, "O_DIRECTORY", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=parent_fd,
                    )
                    os.close(parent_fd)
                    parent_fd = child_fd
                root_stat = os.fstat(root_fd)
                current_root = os.stat(root, follow_symlinks=False)
                if (
                    root_path_stat.st_dev != root_stat.st_dev
                    or root_path_stat.st_ino != root_stat.st_ino
                    or root_stat.st_dev != current_root.st_dev
                    or root_stat.st_ino != current_root.st_ino
                    or (role != "external" and not self._bind_root(role, root))
                ):
                    raise StorageConfigurationError("storage_root_changed")
                if role == "external":
                    self._assert_external_binding_pinned(root_fd, root)
                return root_fd, parent_fd, parts[-1], root, role
            except Exception:
                os.close(parent_fd)
                os.close(root_fd)
                raise
        return None

    def _assert_external_binding_pinned(self, root_fd: int, root: Path) -> None:
        """Verify an external marker through the already-pinned root fd."""
        expected = self._external_root_bindings.get(root)
        if expected is None:
            return
        try:
            # Keep one strict parser for API state, enrollment, and the
            # descriptor-pinned publication seam.
            from app.services.external_library import read_root_marker_fd

            actual = read_root_marker_fd(root_fd)
        except (FileNotFoundError, OSError, UnicodeError, ValueError, TypeError) as exc:
            raise StorageConfigurationError("external_root_binding_changed") from exc
        if actual != expected:
            raise StorageConfigurationError("external_root_binding_changed")

    @staticmethod
    def _assert_pinned_root_current(root_fd: int, root: Path) -> None:
        pinned = os.fstat(root_fd)
        current = os.stat(root, follow_symlinks=False)
        if pinned.st_dev != current.st_dev or pinned.st_ino != current.st_ino:
            raise StorageConfigurationError("storage_root_changed")

    def _owned_namespace(self, path: Path) -> str | None:
        resolved = path.resolve(strict=False)
        roots: list[tuple[str, Path]] = [
            ("data", settings.data_dir),
            ("thumb", settings.thumb_dir),
        ]
        backup_root = getattr(settings, "backup_dir", None)
        if backup_root is not None:
            roots.append(("backup", backup_root))
        roots.extend((f"external:{root}", root) for root in self._external_roots)
        for role, configured_root in roots:
            root = Path(configured_root).resolve(strict=False)
            if resolved == root or resolved.is_relative_to(root):
                return f"{role}:{root}"
        return None

    def namespace_for(self, key: str) -> str:
        namespace = self._owned_namespace(Path(key))
        if namespace is None:
            raise StorageCollisionError("storage_key_outside_managed_root")
        return namespace

    def validate_restore_key(self, key: str) -> None:
        path = self.direct_path(key)
        if path is None:
            raise StorageConfigurationError("local_restore_key_not_a_path")
        target = path.resolve(strict=False)
        roots = (
            Path(settings.data_dir).resolve(strict=False),
            Path(settings.thumb_dir).resolve(strict=False),
            Path(getattr(settings, "backup_dir", settings.data_dir)).resolve(
                strict=False
            ),
            *self._external_roots,
        )
        if not any(target != root and target.is_relative_to(root) for root in roots):
            raise StorageConfigurationError("backup_restore_key_outside_storage")

    def reclaim_unverified(
        self,
        key: str,
        *,
        expected_size: int,
        expected_etag: str | None,
        expected_sha256: str | None = None,
        expected_version_id: str | None = None,
    ) -> bool:
        del expected_version_id
        path = Path(key)
        self._assert_root_binding_for(path)
        self.namespace_for(key)
        info = self.object_info(key)
        if info is None:
            return True
        if info.size != expected_size:
            return False
        if expected_etag is not None and info.etag != expected_etag:
            return False

        # Rename the selected directory entry into a private same-directory
        # quarantine.  Unlike stat-then-unlink (or a hardlink followed by
        # unlink), rename(2) selects one directory entry atomically.  A writer
        # that wins the path race can only create a new entry at ``path``; it
        # cannot cause us to unlink that replacement.
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except FileNotFoundError:
            return True
        try:
            before = os.fstat(fd)
            if before.st_size != expected_size or not stat_module.S_ISREG(
                before.st_mode
            ):
                return False
            digest = hashlib.sha256()
            while chunk := os.read(fd, 1024 * 1024):
                digest.update(chunk)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_ctime_ns != after.st_ctime_ns
            or before.st_size != after.st_size
        ):
            return False
        if (
            expected_sha256 is not None
            and digest.hexdigest() != expected_sha256.lower()
        ):
            return False

        self._assert_root_binding_for(path)
        quarantine = path.parent / f".printstash-reclaim-{uuid.uuid4().hex}"
        parent_fd = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        quarantine_created = False
        try:
            # Reserve the quarantine name without ever replacing an existing
            # entry.  The empty placeholder is the only entry we permit
            # os.replace to overwrite.
            quarantine_fd = os.open(
                quarantine.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent_fd,
            )
            os.close(quarantine_fd)
            quarantine_created = True
            os.rename(
                path.name,
                quarantine.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
        except FileNotFoundError:
            if quarantine_created:
                os.unlink(quarantine.name, dir_fd=parent_fd)
            return True
        except OSError:
            if quarantine_created:
                try:
                    os.unlink(quarantine.name, dir_fd=parent_fd)
                except OSError:
                    pass
            return False
        try:

            def restore_mismatched_quarantine() -> None:
                """Restore moved bytes only when the destination is vacant."""
                nonlocal quarantine_created
                try:
                    os.link(
                        quarantine.name,
                        path.name,
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                        follow_symlinks=False,
                    )
                except FileExistsError:
                    # A concurrent writer owns the destination. Preserve both
                    # entries for reconciliation rather than replacing it.
                    return
                except OSError:
                    return
                try:
                    os.unlink(quarantine.name, dir_fd=parent_fd)
                    quarantine_created = False
                except OSError:
                    logger.warning(
                        "local reclaim quarantine restore cleanup failed",
                        extra={"destination": str(path), "quarantine": str(quarantine)},
                    )

            # The root binding may have changed while the rename was in
            # flight.  Preserve the moved bytes if so; never report a delete
            # from an unproven mount.
            self._assert_root_binding_for(path)
            quarantined = os.stat(
                quarantine.name, dir_fd=parent_fd, follow_symlinks=False
            )
            if (
                quarantined.st_dev != before.st_dev
                or quarantined.st_ino != before.st_ino
                or quarantined.st_size != before.st_size
            ):
                logger.warning(
                    "local reclaim quarantine identity mismatch",
                    extra={"destination": str(path), "quarantine": str(quarantine)},
                )
                restore_mismatched_quarantine()
                return False
            # Re-read and hash the quarantined inode itself.  A replacement
            # moved by a pathname race must remain retained, not be mistaken
            # for the originally verified object.
            quarantine_read_fd = os.open(
                quarantine.name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            with os.fdopen(quarantine_read_fd, "rb") as quarantined_file:
                digest = hashlib.sha256()
                while chunk := quarantined_file.read(1024 * 1024):
                    digest.update(chunk)
            if (
                expected_sha256 is not None
                and digest.hexdigest() != expected_sha256.lower()
            ):
                restore_mismatched_quarantine()
                return False
            os.unlink(quarantine.name, dir_fd=parent_fd)
            quarantine_created = False
            os.fsync(parent_fd)
            return True
        finally:
            # If verification or fsync failed, retain the quarantined bytes
            # for a later operator/reconciliation pass.  The original path is
            # intentionally never unlinked after the atomic rename.
            if quarantine_created:
                logger.warning(
                    "local reclaim quarantine retained for reconciliation",
                    extra={"destination": str(path), "quarantine": str(quarantine)},
                )
            os.close(parent_fd)

    def direct_path(self, key: str) -> Path | None:
        return Path(key)

    @staticmethod
    def _relocated_receipt(receipt: CreationReceipt, path: Path) -> CreationReceipt:
        # rename(2) updates ctime on Linux. Device/inode/size still prove that
        # quarantine captured the same object selected by the preflight check.
        current = path.stat(follow_symlinks=False)
        return replace(receipt, key=str(path), ctime_ns=current.st_ctime_ns)

    def blob_key(self, slug: str, version: int, filename: str) -> str:
        return str(settings.data_dir / slug / f"v{version}" / filename)

    def thumbnail_key(self, file_id: int) -> str:
        return str(settings.thumb_dir / f"{file_id}.webp")

    def source_cover_key(self, provenance_source_id: int) -> str:
        return str(
            settings.thumb_dir / "source-covers" / f"{provenance_source_id}.webp"
        )

    def capture_upload_slot_key(self, slot_id: str) -> str:
        return str(settings.data_dir / "capture-slots" / slot_id)

    def legacy_thumbnail_key(self, file_id: int) -> str:
        return str(settings.thumb_dir / f"{file_id}.png")

    def stl_cache_key(self, sha256: str) -> str:
        return str(settings.thumb_dir / "stl-cache" / f"{sha256}.stl")

    def collection_image_key(self, collection_id: int, name: str) -> str:
        return str(settings.thumb_dir / "collection-images" / str(collection_id) / name)

    def document_file_key(self, document_id: int, name: str) -> str:
        return str(settings.data_dir / "documents" / str(document_id) / name)

    def document_image_key(self, document_id: int, name: str) -> str:
        return str(settings.thumb_dir / "document-images" / str(document_id) / name)

    def multipart_model_cover_key(self, multipart_model_id: int, name: str) -> str:
        return str(
            settings.thumb_dir / "multipart-covers" / str(multipart_model_id) / name
        )

    def exists(self, key: str) -> bool:
        return Path(key).exists()

    def write_stream(self, src: BinaryIO, key: str) -> int:
        return self.create_stream(src, key).size

    def write_bytes(self, data: bytes, key: str) -> int:
        return self.create_bytes(data, key).size

    def _create_stream_pinned(
        self, src: BinaryIO, dest: Path
    ) -> CreationReceipt | None:
        """Publish a managed local object through a pinned directory fd.

        ``Path`` validation alone is not sufficient when a mount can be
        replaced while an upload is being staged.  The parent walk and both
        publication primitives below are descriptor-relative, so a pathname
        switch cannot redirect this operation to another root.
        """
        pinned = self._open_pinned_parent(dest)
        if pinned is None:
            return None
        root_fd, parent_fd, dest_name, root, role = pinned
        temp_name = f".printstash-create-{uuid.uuid4().hex}"
        temp_created = False
        try:
            temp_fd = os.open(
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent_fd,
            )
            temp_created = True
            written = 0
            with os.fdopen(temp_fd, "wb") as staged:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    staged.write(chunk)
                    written += len(chunk)
                staged.flush()
                os.fsync(staged.fileno())

            # Check both the durable marker and the directory identity just
            # before publication.  The final check below handles a remount or
            # marker replacement that occurs during the publication syscall.
            self._assert_root_binding_for(dest)
            self._assert_pinned_root_current(root_fd, root)
            if role == "external":
                self._assert_external_binding_pinned(root_fd, root)
            verified_identity = True
            try:
                os.link(
                    temp_name,
                    dest_name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                    follow_symlinks=False,
                )
            except OSError as exc:
                if isinstance(exc, FileExistsError):
                    raise StorageCollisionError(str(dest)) from exc
                if exc.errno not in {
                    getattr(os, "EXDEV", 18),
                    getattr(os, "EPERM", 1),
                    getattr(os, "EOPNOTSUPP", 95),
                }:
                    raise
                # Hardlinkless mounts retain create-only semantics through
                # O_EXCL.  A later write failure deliberately leaves the
                # destination for reconciliation; it is never blindly
                # unlinked after a possible replacement race.
                verified_identity = False
                try:
                    out_fd = os.open(
                        dest_name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                        0o644,
                        dir_fd=parent_fd,
                    )
                except FileExistsError as collision:
                    raise StorageCollisionError(str(dest)) from collision
                try:
                    with os.fdopen(out_fd, "wb") as out:
                        read_fd = os.open(
                            temp_name,
                            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=parent_fd,
                        )
                        with os.fdopen(read_fd, "rb") as staged:
                            shutil.copyfileobj(staged, out)
                        out.flush()
                        os.fsync(out.fileno())
                except Exception:
                    logger.warning(
                        "guarded local publication left an uncertain destination",
                        extra={"path": str(dest)},
                    )
                    raise
            os.unlink(temp_name, dir_fd=parent_fd)
            temp_created = False
            os.fsync(parent_fd)
            self._assert_pinned_root_current(root_fd, root)
            if role == "external":
                self._assert_external_binding_pinned(root_fd, root)
            if role != "external" and not self._bind_root(role, root):
                raise StorageConfigurationError("storage_root_changed")
            stat_result = os.stat(dest_name, dir_fd=parent_fd, follow_symlinks=False)
            return CreationReceipt(
                key=str(dest),
                size=written,
                token=uuid.uuid4().hex,
                backend="local",
                namespace=self._owned_namespace(dest)
                or f"external:{dest.parent.resolve(strict=False)}",
                device=stat_result.st_dev if verified_identity else None,
                inode=stat_result.st_ino if verified_identity else None,
                ctime_ns=stat_result.st_ctime_ns if verified_identity else None,
            )
        finally:
            if temp_created:
                try:
                    os.unlink(temp_name, dir_fd=parent_fd)
                except OSError:
                    logger.warning(
                        "storage create temp cleanup failed", extra={"path": str(dest)}
                    )
            os.close(parent_fd)
            os.close(root_fd)

    def create_stream(self, src: BinaryIO, key: str) -> CreationReceipt:
        # A remote-compatible subclass may override ``direct_path`` while
        # inheriting this class. Keep it on the generic seam rather than
        # interpreting its opaque key as a local filesystem path.
        if self.direct_path(key) is None:
            return StorageBackend.create_stream(self, src, key)

        if not self.capabilities.conditional_create:
            # A reachable filesystem that cannot prove no-replace publication
            # remains readable but must not accept writes. The setting that
            # acknowledges an unverified provider cannot turn this into a safe
            # mutation path.
            raise StorageConfigurationError("storage_write_unverified")

        dest = Path(key)
        self._assert_root_binding_for(dest)
        pinned_receipt = self._create_stream_pinned(src, dest)
        if pinned_receipt is not None:
            return pinned_receipt
        # On hardlinkless NAS/FUSE mounts, O_EXCL is the only safe create-only
        # primitive available.  It is Guarded (not Verified): the exact inode
        # cannot be proven later for deletion, but concurrent writers can never
        # overwrite one another.
        # Backup archives are application-owned staging/output, not a legacy
        # vault root.  They are created by the backup service immediately
        # before publication and therefore intentionally have no vault
        # binding marker.
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".printstash-create-", dir=dest.parent)
        temp = Path(temp_name)
        written = 0
        try:
            with os.fdopen(fd, "wb") as out:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
                out.flush()
                os.fsync(out.fileno())
            # Revalidate the mount after staging bytes and immediately before
            # publication. This closes the common split-brain window where a
            # bind mount is replaced while a slow upload is in progress.
            self._assert_root_binding_for(dest)
            try:
                # link(2) is an atomic no-replace publication on the same
                # filesystem. Readers never observe the partial temp file.
                os.link(temp, dest, follow_symlinks=False)
            except OSError as exc:
                if isinstance(exc, FileExistsError):
                    raise StorageCollisionError(str(dest)) from exc
                # The temp file has already been fully fsynced. Fall back to
                # direct O_EXCL only when link(2) itself is unavailable; any
                # other publication failure remains fatal.
                if exc.errno not in {
                    getattr(os, "EXDEV", 18),
                    getattr(os, "EPERM", 1),
                    getattr(os, "EOPNOTSUPP", 95),
                }:
                    raise
                try:
                    out_fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                except FileExistsError as collision:
                    raise StorageCollisionError(str(dest)) from collision
                try:
                    with os.fdopen(out_fd, "wb") as out:
                        with temp.open("rb") as staged:
                            shutil.copyfileobj(staged, out)
                        out.flush()
                        os.fsync(out.fileno())
                except Exception:
                    # O_EXCL proves this operation opened the path, but a
                    # subsequent failure does not prove which bytes are at the
                    # name. Preserve the partial object for reconciliation;
                    # an unconditional unlink could remove a raced replacement.
                    logger.warning(
                        "guarded local publication left an uncertain destination",
                        extra={"path": str(dest)},
                    )
                    raise
                # A directory fsync is best effort for Guarded fallback.  The
                # capability probe advertises the weaker tier explicitly.
                try:
                    _fsync_directory(dest.parent)
                except OSError:
                    logger.warning(
                        "guarded local publication directory fsync failed",
                        extra={"path": str(dest)},
                    )
                return CreationReceipt(
                    key=str(dest),
                    size=written,
                    token=uuid.uuid4().hex,
                    backend="local",
                    namespace=self._owned_namespace(dest)
                    or f"external:{dest.parent.resolve(strict=False)}",
                )
            # Dropping the temporary hard link changes ctime/link-count.
            temp.unlink()
            _fsync_directory(dest.parent)
            stat = dest.stat(follow_symlinks=False)
            return CreationReceipt(
                key=str(dest),
                size=written,
                token=uuid.uuid4().hex,
                backend="local",
                namespace=self._owned_namespace(dest)
                or f"external:{dest.parent.resolve(strict=False)}",
                device=stat.st_dev,
                inode=stat.st_ino,
                ctime_ns=stat.st_ctime_ns,
            )
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "storage create temp cleanup failed", extra={"path": str(temp)}
                )

    def _quarantine_owned(self, receipt: CreationReceipt) -> Path | None:
        """Move the current exact inode aside before any unlink or replacement.

        POSIX has no unlink-if-inode-still-matches primitive. A check followed
        by unlink has a TOCTOU window that could remove a newly mounted or
        concurrently replaced path. Renaming into a random same-directory
        quarantine is atomic and non-destructive; only the moved inode is then
        eligible for deletion.
        """
        if not self.creation_matches(receipt):
            return None
        dest = Path(receipt.key)
        self._assert_root_binding_for(dest)
        fd, quarantine_name = tempfile.mkstemp(
            prefix=".printstash-quarantine-", dir=dest.parent
        )
        os.close(fd)
        quarantine = Path(quarantine_name)
        moved = False
        try:
            # The only overwritten inode is the empty placeholder just created
            # by this operation. Whichever inode is at dest is preserved at the
            # quarantine path for a second proof check.
            os.replace(dest, quarantine)
            moved = True
            moved_receipt = self._relocated_receipt(receipt, quarantine)
            if self.creation_matches(moved_receipt):
                return quarantine

            # The path changed after the first check. Restore without replacing
            # anything that may now occupy the original destination.
            try:
                os.link(quarantine, dest, follow_symlinks=False)
            except FileExistsError as exc:
                logger.critical(
                    "storage quarantine preserved a raced object for recovery",
                    extra={"destination": str(dest), "quarantine": str(quarantine)},
                )
                raise StorageCollisionError(str(dest)) from exc
            quarantine.unlink()
            moved = False
            return None
        finally:
            if not moved:
                quarantine.unlink(missing_ok=True)

    def rollback_create(self, receipt: CreationReceipt) -> bool:
        if not self.capabilities.verified_delete:
            logger.warning(
                "storage rollback skipped: local filesystem identity is not stable",
                extra={"key": receipt.key},
            )
            return False
        quarantine = self._quarantine_owned(receipt)
        if quarantine is None:
            logger.warning(
                "storage rollback skipped: destination no longer matches receipt",
                extra={"key": receipt.key},
            )
            return False
        moved_receipt = self._relocated_receipt(receipt, quarantine)
        if not self.creation_matches(moved_receipt):
            logger.critical(
                "storage quarantine changed before deletion; preserving it",
                extra={"quarantine": str(quarantine)},
            )
            return False
        quarantine.unlink()
        return True

    def replace_stream(
        self, src: BinaryIO, receipt: CreationReceipt
    ) -> CreationReceipt:
        if not self.capabilities.conditional_replace:
            raise NotImplementedError("atomic_replace_not_supported")
        dest = Path(receipt.key)
        self._assert_root_binding_for(dest)
        fd, temp_name = tempfile.mkstemp(prefix=".printstash-replace-", dir=dest.parent)
        temp = Path(temp_name)
        written = 0
        try:
            with os.fdopen(fd, "wb") as out:
                while chunk := src.read(1024 * 1024):
                    out.write(chunk)
                    written += len(chunk)
                out.flush()
                os.fsync(out.fileno())
            quarantine = self._quarantine_owned(receipt)
            if quarantine is None:
                raise StorageCollisionError(receipt.key)
            try:
                # Atomic no-replace publication. If another process claims the
                # path after quarantine, both its file and our old owned inode
                # survive; the replacement aborts.
                os.link(temp, dest, follow_symlinks=False)
            except FileExistsError as exc:
                logger.critical(
                    "storage replacement collision preserved old quarantine",
                    extra={"destination": str(dest), "quarantine": str(quarantine)},
                )
                raise StorageCollisionError(receipt.key) from exc
            temp.unlink()
            stat_result = dest.stat(follow_symlinks=False)
            replacement_receipt = CreationReceipt(
                key=str(dest),
                size=written,
                token=uuid.uuid4().hex,
                backend="local",
                namespace=receipt.namespace,
                device=stat_result.st_dev,
                inode=stat_result.st_ino,
                ctime_ns=stat_result.st_ctime_ns,
            )
            try:
                quarantine.unlink()
            except OSError:
                # The new object and receipt are durable. Preserve an uncertain
                # old quarantine rather than failing and orphaning the new one.
                logger.exception(
                    "storage replacement left an owned quarantine",
                    extra={"quarantine": str(quarantine)},
                )
            return replacement_receipt
        finally:
            temp.unlink(missing_ok=True)

    def creation_matches(self, receipt: CreationReceipt) -> bool:
        if self.capabilities.object_identity is not ObjectIdentity.INODE:
            return False
        if receipt.backend != "local":
            return False
        path = Path(receipt.key)
        current_namespace = self._owned_namespace(path)
        if current_namespace is None or current_namespace != receipt.namespace:
            logger.warning(
                "storage delete skipped: key is outside its recorded current root",
                extra={"key": receipt.key},
            )
            return False
        try:
            stat = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return False
        if (
            stat.st_dev != receipt.device
            or stat.st_ino != receipt.inode
            or stat.st_ctime_ns != receipt.ctime_ns
            or stat.st_size != receipt.size
        ):
            return False
        return True

    def adopt_existing(
        self, key: str, *, expected_size: int, expected_sha256: str
    ) -> CreationReceipt:
        """Adopt one pre-ledger local Artifact after content + inode proof.

        The open uses ``O_NOFOLLOW`` where available and hashes through the
        descriptor. Matching ``fstat`` snapshots before/after ensure the bytes
        did not change while they were verified. The resulting receipt then
        uses the same device/inode/ctime guard as a newly-created object.
        """
        path = Path(key)
        self._assert_root_binding_for(path)
        namespace = self._owned_namespace(path)
        if namespace is None:
            raise StorageCollisionError("storage_key_outside_managed_root")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise StorageCollisionError("legacy_storage_object_unavailable") from exc
        try:
            before = os.fstat(fd)
            if (
                not stat_module.S_ISREG(before.st_mode)
                or before.st_size != expected_size
            ):
                raise StorageCollisionError("legacy_storage_content_mismatch")
            digest = hashlib.sha256()
            while chunk := os.read(fd, 1024 * 1024):
                digest.update(chunk)
            after = os.fstat(fd)
        finally:
            os.close(fd)
        identity = (before.st_dev, before.st_ino, before.st_ctime_ns, before.st_size)
        if identity != (after.st_dev, after.st_ino, after.st_ctime_ns, after.st_size):
            raise StorageCollisionError("legacy_storage_object_changed")
        if digest.hexdigest() != expected_sha256.lower():
            raise StorageCollisionError("legacy_storage_content_mismatch")
        receipt = CreationReceipt(
            key=str(path),
            size=after.st_size,
            token=expected_sha256.lower(),
            backend="local",
            namespace=namespace,
            device=after.st_dev,
            inode=after.st_ino,
            ctime_ns=after.st_ctime_ns,
        )
        if not self.creation_matches(receipt):
            raise StorageCollisionError("legacy_storage_object_changed")
        return receipt

    def verify_destructive_access(self, keys: list[str]) -> None:
        # Probe every distinct parent because nested ACLs/read-only submounts
        # can differ beneath one configured root. mkstemp is O_EXCL: cleanup
        # targets only the inode this probe just created.
        if any(self.direct_path(key) is None for key in keys):
            return super().verify_destructive_access(keys)
        paths = [Path(key) for key in keys]
        for path in paths:
            self._assert_root_binding_for(path)
        for parent in {path.parent for path in paths}:
            fd, probe_name = tempfile.mkstemp(
                prefix=".printstash-delete-probe-", dir=parent
            )
            os.close(fd)
            Path(probe_name).unlink()

    def move(self, src_key: str, dest_key: str) -> None:
        del src_key, dest_key
        raise RuntimeError("unchecked_storage_move_disabled")

    def stat_size(self, key: str) -> int:
        return Path(key).stat().st_size

    def object_info(self, key: str) -> StorageObjectInfo | None:
        try:
            stat = Path(key).stat()
        except FileNotFoundError:
            return None
        return StorageObjectInfo(
            size=stat.st_size,
            etag=f'"{stat.st_mtime_ns:x}-{stat.st_size:x}"',
        )

    def read_bytes(self, key: str) -> bytes:
        return Path(key).read_bytes()

    def stream_chunks(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        with Path(key).open("rb") as fh:
            while True:
                chunk = fh.read(chunk_size)
                if not chunk:
                    break
                yield chunk

    def download_to_path(self, key: str, dest: Path) -> Path:
        self._assert_root_binding_for(dest)
        with Path(key).open("rb") as source:
            return _copy_stream_create_only(source, dest)

    def upload_file(self, src: Path, key: str) -> None:
        with src.open("rb") as source:
            self.create_stream(source, key)

    def ensure_setup(self) -> None:
        # Never mkdir a configured vault root. A missing bind mount must leave
        # the application readable/recoverable rather than writing into a new
        # directory in the container or host filesystem.
        self._startup_checked = True
        configured_roots = {
            "data": Path(settings.data_dir).expanduser(),
            "thumb": Path(settings.thumb_dir).expanduser(),
        }
        missing = [role for role, root in configured_roots.items() if not root.is_dir()]
        self._root_binding_diagnostics = {role: "missing" for role in missing}
        self._roots_ready = not missing and all(
            self._bind_root(role, root) for role, root in configured_roots.items()
        )
        if not self._roots_ready:
            self.recovery_mode = True
            self._capabilities = StorageCapabilities(
                conditional_create=False,
                object_identity=ObjectIdentity.NONE,
                verified_delete=False,
                conditional_replace=False,
                namespace_ownership=True,
                direct_path=True,
            )
            self._probe_diagnostics = {
                "probed": True,
                "roots_ready": False,
                "root_bindings": self._root_binding_diagnostics,
            }
            return
        self.recovery_mode = False
        roots = (
            self._probe_root("data", settings.data_dir),
            self._probe_root("thumb", settings.thumb_dir),
        )
        hardlinks = all(root.hardlink for root in roots)
        exclusive_create = all(root.exclusive_create for root in roots)
        directory_fsync = all(root.directory_fsync for root in roots)
        stable_inodes = (
            hardlinks
            and directory_fsync
            and all(root.fs_kind == "local" for root in roots)
        )
        self._capabilities = StorageCapabilities(
            conditional_create=hardlinks or exclusive_create,
            object_identity=(
                ObjectIdentity.INODE if stable_inodes else ObjectIdentity.NONE
            ),
            verified_delete=stable_inodes,
            conditional_replace=stable_inodes,
            namespace_ownership=True,
            direct_path=True,
        )
        self._probe_diagnostics = {
            "probed": True,
            "roots_ready": True,
            "directory_fsync": directory_fsync,
            "root_bindings": self._root_binding_diagnostics,
            "roots": [root.as_dict() for root in roots],
        }

    def delete(self, key: str) -> None:
        del key
        raise RuntimeError("unchecked_storage_delete_disabled")

    def list_keys(self, prefix: str = "") -> list[str]:
        root = Path(prefix) if prefix else settings.data_dir
        if not root.exists():
            return []
        return [
            str(p)
            for p in root.rglob("*")
            if p.is_file() and p.name != self._BINDING_FILENAME
        ]

    def walk_keys(self, prefix: str = "") -> Iterator[str]:
        root = Path(prefix) if prefix else settings.data_dir
        if not root.exists():
            return
        for p in root.rglob("*"):
            if p.is_file() and p.name != self._BINDING_FILENAME:
                yield str(p)

    def usage(self, prefix: str = "") -> dict:
        root = Path(prefix) if prefix else settings.data_dir
        total_size = 0
        object_count = 0
        if root.exists():
            for path in root.rglob("*"):
                if not path.is_file() or path.name == self._BINDING_FILENAME:
                    continue
                try:
                    total_size += path.stat().st_size
                    object_count += 1
                except OSError:
                    continue
        return {
            "backend": "local",
            "prefix": str(root),
            "object_count": object_count,
            "total_size_bytes": total_size,
        }

    def presigned_download_url(self, key: str, filename: str) -> str | None:
        return None

    def health_probe(self) -> dict:
        # Re-read both sentinels so a mount disappearing after startup is
        # reflected immediately. This is validation only; `_bind_root` never
        # enrolls or creates a marker.
        data_root = Path(settings.data_dir).expanduser()
        thumb_root = Path(settings.thumb_dir).expanduser()
        data_ok = data_root.is_dir() and self._bind_root("data", data_root)
        thumb_ok = thumb_root.is_dir() and self._bind_root("thumb", thumb_root)
        self._roots_ready = data_ok and thumb_ok
        return {
            "backend": "local",
            "ok": data_ok and thumb_ok and self._roots_ready,
            "data_dir": str(settings.data_dir),
            "thumb_dir": str(settings.thumb_dir),
            "capabilities": self.capabilities.as_dict(),
            "diagnostics": self.probe_diagnostics,
        }


# ---------------------------------------------------------------------------
# Legacy local-root enrollment
# ---------------------------------------------------------------------------


def enroll_legacy_local_root(
    root: Path,
    *,
    role: str,
    installation: str,
    proofs: list[tuple[Path, int, str | None]],
    allow_empty: bool = False,
    allow_size_only: bool = False,
) -> bool:
    """Enroll a pre-ledger root only after deterministic content proof.

    Existing v0.12 roots have no marker. A non-empty root is accepted only when
    every selected DB-referenced object still has its recorded size and hash.
    Empty roots are accepted solely for a brand-new, unconfigured installation.
    """
    # Callers may provide a path-like test double or another pathlib-compatible
    # wrapper.  Normalize at this boundary so marker I/O always uses the
    # backend's validated filesystem seam rather than relying on wrapper
    # implementation details.
    root = Path(str(root))
    if not root.is_dir():
        return False
    marker = root / LocalStorageBackend._BINDING_FILENAME
    expected = {
        "format": LocalStorageBackend._BINDING_FORMAT,
        "installation": installation,
        "role": role,
    }
    legacy_marker = False
    try:
        actual = json.loads(marker.read_text(encoding="utf-8"))
        if (
            isinstance(actual, dict)
            and type(actual.get("format")) is int
            and actual == expected
        ):
            return True
        if actual != {"installation": installation, "role": role}:
            return False
        legacy_marker = True
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError):
        return False
    if not proofs and not allow_empty:
        return False
    for path, expected_size, expected_sha256 in proofs:
        # A size-only claim cannot distinguish the intended legacy mount from
        # an unrelated file tree.  The sole exception is the thumbnail root
        # physically co-located under an already hash-proven data root; the
        # caller sets that explicit narrow flag.
        if not allow_size_only and (
            not expected_sha256
            or len(expected_sha256) != 64
            or any(char not in "0123456789abcdefABCDEF" for char in expected_sha256)
        ):
            return False
        try:
            candidate = path.expanduser().resolve(strict=True)
            boundary = root.resolve(strict=True)
            if candidate == boundary or not candidate.is_relative_to(boundary):
                return False
            stat = candidate.stat()
            if stat.st_size != expected_size:
                return False
            if expected_sha256 is not None:
                digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
                if digest != expected_sha256.lower():
                    return False
        except OSError:
            return False
    marker_to_write = marker
    temporary_marker: Path | None = None
    if legacy_marker:
        # Upgrade an explicitly proven pre-format marker atomically.  Re-read
        # it first so an administrator or another process cannot have changed
        # the binding while the content proofs were being computed.
        try:
            if json.loads(marker.read_text(encoding="utf-8")) != {
                "installation": installation,
                "role": role,
            }:
                return False
        except (OSError, ValueError, TypeError):
            return False
        temporary_marker = marker.with_name(f".{marker.name}.{uuid.uuid4().hex}.tmp")
        marker_to_write = temporary_marker
    try:
        with marker_to_write.open("x", encoding="utf-8") as handle:
            json.dump(expected, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        if temporary_marker is not None:
            os.replace(temporary_marker, marker)
        _fsync_directory(root)
    except FileExistsError:
        if temporary_marker is not None:
            temporary_marker.unlink(missing_ok=True)
        try:
            return json.loads(marker.read_text(encoding="utf-8")) == expected
        except (OSError, ValueError, TypeError):
            return False
    except OSError:
        if temporary_marker is not None:
            temporary_marker.unlink(missing_ok=True)
        return False
    return True


# ---------------------------------------------------------------------------
# S3-compatible backend (AWS S3, Cloudflare R2, SeaweedFS, MinIO, etc.)
# ---------------------------------------------------------------------------


_S3_MISSING_OBJECT_CODES = {"404", "NoSuchKey", "NotFound"}


def _raise_s3_missing_object(exc: Exception, key: str) -> None:
    """Translate only object-missing responses; preserve all other failures."""

    code = exc.response.get("Error", {}).get("Code")  # type: ignore[attr-defined]
    if code in _S3_MISSING_OBJECT_CODES:
        raise FileNotFoundError(key) from exc
    raise exc


class S3StorageBackend(StorageBackend):
    backend_name = "s3"
    transport = "s3"

    def __init__(self, *, check_bucket: bool = True) -> None:
        import boto3
        from botocore.config import Config as BotoConfig

        if not settings.s3_bucket:
            raise RuntimeError("VAULT_S3_BUCKET is required when storage_backend=s3")

        # The root is part of this adapter's identity.  Capture it once so a
        # second runtime overlay/backend cannot make an already-live adapter
        # read, list, or reclaim the other installation's namespace.
        self._s3_root = self._normalized_root(
            str(getattr(settings, "s3_root", "vault-data") or "vault-data")
        )
        # Provider identity is derived from the adapter's immutable target,
        # never from mutable runtime settings after composition.
        self._endpoint_url = str(getattr(settings, "s3_endpoint_url", "") or "")
        self._region = str(getattr(settings, "s3_region", "auto") or "auto")
        addressing_style = str(
            getattr(settings, "s3_addressing_style", "auto") or "auto"
        )
        if addressing_style not in {"auto", "path", "virtual"}:
            raise StorageConfigurationError("s3_addressing_style_invalid")
        if (
            addressing_style == "auto"
            and str(getattr(settings, "storage_provider", "")) == "s3_self_hosted"
        ):
            addressing_style = "path"
        self._addressing_style = addressing_style

        client_kwargs: dict = {
            "service_name": "s3",
            "region_name": settings.s3_region or "auto",
            "aws_access_key_id": settings.s3_access_key or None,
            "aws_secret_access_key": settings.s3_secret_key or None,
            "config": BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": addressing_style},
            ),
        }
        if settings.s3_endpoint_url:
            client_kwargs["endpoint_url"] = settings.s3_endpoint_url

        self._client = boto3.client(**client_kwargs)
        self._bucket = settings.s3_bucket
        self.provider_id = str(getattr(settings, "storage_provider", "") or "s3")
        self._capabilities = StorageCapabilities(
            conditional_create=True,
            object_identity=ObjectIdentity.ETAG,
            verified_delete=False,
            conditional_replace=True,
            namespace_ownership=True,
            direct_path=False,
        )
        self._probe_diagnostics: dict[str, object] = {
            "probed": False,
            "bucket_versioning": "unknown",
        }
        self._read_only = False

        # Recovery startup must not perform a network probe before the
        # unresolved restore journal is inspected/resumed.  Reads remain
        # available and the restore path performs its own operation checks.
        if check_bucket:
            self._ensure_bucket()

    def _probe_capabilities(self) -> None:
        status = "unknown"
        try:
            response = self._client.get_bucket_versioning(Bucket=self._bucket)
            status = str(response.get("Status") or "absent").lower()
        except Exception as exc:
            logger.warning("S3 versioning probe failed", exc_info=True)
            self._probe_diagnostics = {
                "probed": True,
                "bucket_versioning": status,
                "versioning_error": exc.__class__.__name__,
            }
        else:
            self._probe_diagnostics = {
                "probed": True,
                "bucket_versioning": status,
            }
        versioned = status == "enabled"
        conditional = self._probe_conditional_create()
        self._capabilities = StorageCapabilities(
            conditional_create=conditional,
            object_identity=(
                ObjectIdentity.VERSION if versioned else ObjectIdentity.ETAG
            ),
            verified_delete=versioned,
            conditional_replace=True,
            namespace_ownership=True,
            direct_path=False,
        )
        self._read_only = not conditional
        self._probe_diagnostics["conditional_create"] = conditional
        if not conditional:
            self._probe_diagnostics["read_only"] = True

    def _probe_conditional_create(self) -> bool:
        """Prove native S3 no-replace semantics with a disposable object."""
        import botocore.exceptions

        # Small in-process clients used by storage unit tests model versioning
        # only. The boto3 production client always exposes this API; retaining
        # their measured versioning path keeps the adapter seam testable.
        if not hasattr(self._client, "put_object"):
            return True

        key = f"{self._prefix()}.printstash-probe/{uuid.uuid4().hex}"
        payload = b"printstash-s3-conditional-create-proof"
        version_id: str | None = None
        try:
            created = self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=payload,
                IfNoneMatch="*",
            )
            if isinstance(created, dict) and created.get("VersionId"):
                version_id = str(created["VersionId"])
            try:
                self._client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=b"replacement",
                    IfNoneMatch="*",
                )
            except botocore.exceptions.ClientError as exc:
                code = str(exc.response.get("Error", {}).get("Code", ""))
                collision = code in {"412", "PreconditionFailed"}
            else:
                collision = False
            observed = self._client.get_object(Bucket=self._bucket, Key=key)[
                "Body"
            ].read()
            if observed != payload:
                collision = False
            return collision
        except Exception as exc:
            raise StorageConfigurationError(
                "s3_conditional_create_unavailable"
            ) from exc
        finally:
            try:
                cleanup = {"Bucket": self._bucket, "Key": key}
                if version_id is not None:
                    cleanup["VersionId"] = version_id
                self._client.delete_object(**cleanup)
            except Exception:
                logger.warning(
                    "S3 conditional-create probe cleanup failed", exc_info=True
                )

    def namespace_for(self, key: str) -> str:
        prefix = self._prefix()
        if not key.startswith(prefix):
            raise StorageCollisionError("storage_key_outside_managed_root")
        return f"{self._bucket}/{prefix}"

    def _validate_managed_key(self, key: str) -> str:
        """Require every S3 object operation to stay in this typed root."""
        self.namespace_for(key)
        return key

    def _validate_managed_prefix(self, prefix: str) -> str:
        managed = self._prefix()
        full_prefix = prefix or managed
        if not full_prefix.startswith(managed):
            raise StorageCollisionError("storage_key_outside_managed_root")
        return full_prefix

    def reclaim_unverified(
        self,
        key: str,
        *,
        expected_size: int,
        expected_etag: str | None,
        expected_sha256: str | None = None,
        expected_version_id: str | None = None,
    ) -> bool:
        self.namespace_for(key)
        if expected_version_id is not None:
            import botocore.exceptions

            try:
                response = self._client.head_object(
                    Bucket=self._bucket,
                    Key=key,
                    VersionId=expected_version_id,
                )
            except botocore.exceptions.ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in {
                    "404",
                    "NoSuchKey",
                    "NoSuchVersion",
                    "NotFound",
                }:
                    return True
                raise
            etag = response.get("ETag")
            if etag and not str(etag).startswith('"'):
                etag = f'"{etag}"'
            if int(response.get("ContentLength", 0) or 0) != expected_size:
                return False
            if expected_etag is not None and str(etag) != expected_etag:
                return False
            self._client.delete_object(
                Bucket=self._bucket,
                Key=key,
                VersionId=expected_version_id,
            )
            return True
        info = self.object_info(key)
        if info is None:
            return True
        if info.size != expected_size:
            return False
        if expected_etag is not None and info.etag != expected_etag:
            return False
        if expected_sha256 is not None:
            digest = hashlib.sha256()
            for chunk in self.stream_chunks(key):
                digest.update(chunk)
            if digest.hexdigest() != expected_sha256.lower():
                return False
        self._client.delete_object(Bucket=self._bucket, Key=key)
        return True

    def destructive_lifecycle_findings(self) -> list[dict[str, object]]:
        import botocore.exceptions

        try:
            response = self._client.get_bucket_lifecycle_configuration(
                Bucket=self._bucket
            )
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {
                "404",
                "NoSuchLifecycleConfiguration",
                "NoSuchLifecycle",
            }:
                return []
            logger.warning("S3 lifecycle audit failed", exc_info=True)
            return []
        managed_prefix = self._prefix()
        findings: list[dict[str, object]] = []
        for rule in response.get("Rules", []):
            if rule.get("Status") != "Enabled" or "Expiration" not in rule:
                continue
            filter_value = rule.get("Filter") or {}
            prefix = str(filter_value.get("Prefix", rule.get("Prefix", "")))
            if managed_prefix.startswith(prefix) or prefix.startswith(managed_prefix):
                findings.append(
                    {
                        "rule_id": str(rule.get("ID", "unnamed")),
                        "prefix": prefix,
                        "expiration": rule["Expiration"],
                    }
                )
        return findings

    def _ensure_bucket(self) -> None:
        import botocore.exceptions

        try:
            self._client.head_bucket(Bucket=self._bucket)
            logger.info("s3: bucket %r found", self._bucket)
        except botocore.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("404", "NoSuchBucket", "NotFound"):
                raise StorageConfigurationError(
                    f"S3 bucket {self._bucket!r} does not exist; create it with "
                    "your storage provider and grant PrintStash object access"
                ) from exc
            raise StorageConfigurationError(
                f"S3 bucket {self._bucket!r} is not accessible; verify the "
                "endpoint, region, credentials, and bucket permissions"
            ) from exc

    def _prefix(self) -> str:
        value = getattr(self, "_s3_root", None)
        if value is None:
            # Test doubles built with ``object.__new__`` predate the captured
            # identity. Real instances always take the immutable branch above.
            value = str(getattr(settings, "s3_root", "vault-data") or "vault-data")
        return f"{value}/"

    @staticmethod
    def _normalized_root(value: str) -> str:
        value = value.strip().strip("/")
        if (
            not value
            or value in {".", ".."}
            or any(part in {"", ".", ".."} for part in Path(value).parts)
        ):
            raise StorageConfigurationError("s3_root_invalid")
        return value

    def direct_path(self, key: str) -> Path | None:
        return None

    def blob_key(self, slug: str, version: int, filename: str) -> str:
        return f"{self._prefix()}files/{slug}/v{version}/{filename}"

    def thumbnail_key(self, file_id: int) -> str:
        return f"{self._prefix()}thumbs/{file_id}.webp"

    def source_cover_key(self, provenance_source_id: int) -> str:
        return f"{self._prefix()}source-covers/{provenance_source_id}.webp"

    def capture_upload_slot_key(self, slot_id: str) -> str:
        return f"{self._prefix()}capture-slots/{slot_id}"

    def legacy_thumbnail_key(self, file_id: int) -> str:
        return f"{self._prefix()}thumbs/{file_id}.png"

    def stl_cache_key(self, sha256: str) -> str:
        return f"{self._prefix()}stl-cache/{sha256}.stl"

    def collection_image_key(self, collection_id: int, name: str) -> str:
        return f"{self._prefix()}collection-images/{collection_id}/{name}"

    def document_file_key(self, document_id: int, name: str) -> str:
        return f"{self._prefix()}documents/{document_id}/{name}"

    def document_image_key(self, document_id: int, name: str) -> str:
        return f"{self._prefix()}document-images/{document_id}/{name}"

    def multipart_model_cover_key(self, multipart_model_id: int, name: str) -> str:
        return f"{self._prefix()}multipart-covers/{multipart_model_id}/{name}"

    def exists(self, key: str) -> bool:
        return self.object_info(key) is not None

    def object_info(self, key: str) -> StorageObjectInfo | None:
        import botocore.exceptions

        self._validate_managed_key(key)
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=key)
        except botocore.exceptions.ClientError as exc:
            # Only a genuine "not there" is False. Credential, permission and
            # network errors must raise: callers need to distinguish an absent
            # object from a storage backend they cannot inspect.
            if exc.response.get("Error", {}).get("Code") in (
                "404",
                "NoSuchKey",
                "NotFound",
            ):
                return None
            raise
        etag = response.get("ETag")
        if etag and not str(etag).startswith('"'):
            etag = f'"{etag}"'
        return StorageObjectInfo(
            size=int(response.get("ContentLength", 0) or 0),
            etag=str(etag) if etag else None,
        )

    def write_stream(self, src: BinaryIO, key: str) -> int:
        return self.create_stream(src, key).size

    def write_bytes(self, data: bytes, key: str) -> int:
        return self.create_bytes(data, key).size

    def create_stream(self, src: BinaryIO, key: str) -> CreationReceipt:
        import botocore.exceptions

        self._validate_managed_key(key)
        if getattr(self, "_read_only", False):
            raise StorageConfigurationError("remote_storage_read_only")

        token = uuid.uuid4().hex
        threshold = int(settings.s3_multipart_threshold_mb) * 1024 * 1024
        spool = tempfile.SpooledTemporaryFile(max_size=threshold)
        shutil.copyfileobj(src, spool, length=1024 * 1024)
        size = spool.tell()
        spool.seek(0)
        try:
            if size > threshold:
                try:
                    response = self._multipart_create(spool, key=key, token=token)
                except botocore.exceptions.ParamValidationError:
                    spool.seek(0)
                    response = self._client.put_object(
                        Bucket=self._bucket,
                        Key=key,
                        Body=spool,
                        IfNoneMatch="*",
                        Metadata={"printstash-create-token": token},
                    )
            else:
                response = self._client.put_object(
                    Bucket=self._bucket,
                    Key=key,
                    Body=spool,
                    IfNoneMatch="*",
                    Metadata={"printstash-create-token": token},
                )
        except botocore.exceptions.ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in {
                "412",
                "PreconditionFailed",
                "ConditionalRequestConflict",
            } or status in {409, 412}:
                raise StorageCollisionError(key) from exc
            raise
        finally:
            spool.close()
        info = self.object_info(key)
        if info is None:
            raise RuntimeError(f"storage create could not verify destination: {key}")
        etag = response.get("ETag") or info.etag
        return CreationReceipt(
            key=key,
            size=info.size,
            token=token,
            backend="s3",
            namespace=f"{self._bucket}/{self._prefix()}",
            etag=str(etag) if etag else None,
            version_id=(
                str(response["VersionId"]) if response.get("VersionId") else None
            ),
        )

    def _multipart_create(
        self,
        src: tempfile.SpooledTemporaryFile[bytes],
        *,
        key: str,
        token: str,
    ) -> dict:
        """Publish multipart data create-only, aborting every incomplete upload."""
        created = self._client.create_multipart_upload(
            Bucket=self._bucket,
            Key=key,
            Metadata={"printstash-create-token": token},
        )
        upload_id = created["UploadId"]
        parts: list[dict[str, object]] = []
        try:
            part_number = 1
            while chunk := src.read(8 * 1024 * 1024):
                uploaded = self._client.upload_part(
                    Bucket=self._bucket,
                    Key=key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    Body=chunk,
                )
                parts.append({"ETag": uploaded["ETag"], "PartNumber": part_number})
                part_number += 1
            return self._client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
                IfNoneMatch="*",
            )
        except Exception:
            try:
                self._client.abort_multipart_upload(
                    Bucket=self._bucket, Key=key, UploadId=upload_id
                )
            except Exception:
                logger.exception("S3 multipart abort failed", extra={"key": key})
            raise

    def rollback_create(self, receipt: CreationReceipt) -> bool:
        # Validate the opaque key before inspecting receipt metadata or making
        # any remote request.  A forged receipt from another typed root must
        # never be able to probe or delete that root's version.
        self._validate_managed_key(receipt.key)
        if not receipt.version_id:
            if self.object_info(receipt.key) is None:
                return True
            logger.warning(
                "storage delete blocked: S3 object has no immutable version identity",
                extra={"key": receipt.key},
            )
            return False
        import botocore.exceptions

        try:
            self._client.head_object(
                Bucket=self._bucket,
                Key=receipt.key,
                VersionId=receipt.version_id,
            )
        except botocore.exceptions.ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {
                "404",
                "NoSuchKey",
                "NoSuchVersion",
                "NotFound",
            }:
                return True
            raise
        if not self.creation_matches(receipt):
            return False
        kwargs = {
            "Bucket": self._bucket,
            "Key": receipt.key,
            "VersionId": receipt.version_id,
        }
        self._client.delete_object(**kwargs)
        return True

    def replace_stream(
        self, src: BinaryIO, receipt: CreationReceipt
    ) -> CreationReceipt:
        import botocore.exceptions

        if not receipt.etag or not self.creation_matches(receipt):
            raise StorageCollisionError(receipt.key)
        token = uuid.uuid4().hex
        try:
            response = self._client.put_object(
                Bucket=self._bucket,
                Key=receipt.key,
                Body=src,
                IfMatch=receipt.etag,
                Metadata={"printstash-create-token": token},
            )
        except botocore.exceptions.ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if code in {
                "412",
                "PreconditionFailed",
                "ConditionalRequestConflict",
            } or status_code in {
                409,
                412,
            }:
                raise StorageCollisionError(receipt.key) from exc
            raise
        info = self.object_info(receipt.key)
        if info is None:
            raise RuntimeError("storage_replace_verification_failed")
        etag = response.get("ETag") or info.etag
        return CreationReceipt(
            key=receipt.key,
            size=info.size,
            token=token,
            backend="s3",
            namespace=receipt.namespace,
            etag=str(etag) if etag else None,
            version_id=(
                str(response["VersionId"]) if response.get("VersionId") else None
            ),
        )

    def creation_matches(self, receipt: CreationReceipt) -> bool:
        if (
            receipt.backend != "s3"
            or receipt.namespace != f"{self._bucket}/{self._prefix()}"
        ):
            return False
        self._validate_managed_key(receipt.key)
        try:
            kwargs = {"Bucket": self._bucket, "Key": receipt.key}
            if receipt.version_id:
                kwargs["VersionId"] = receipt.version_id
            response = self._client.head_object(**kwargs)
        except Exception:
            raise
        metadata = response.get("Metadata", {})
        if metadata.get("printstash-create-token") != receipt.token:
            logger.warning(
                "storage rollback skipped: remote token no longer matches receipt",
                extra={"key": receipt.key},
            )
            return False
        if int(response.get("ContentLength", -1)) != receipt.size:
            return False
        if receipt.etag and str(response.get("ETag", "")) != receipt.etag:
            return False
        return True

    def adopt_existing(
        self, key: str, *, expected_size: int, expected_sha256: str
    ) -> CreationReceipt:
        """Recover a pending S3 publication with content and token proof."""
        import botocore.exceptions

        self._validate_managed_key(key)
        try:
            head = self._client.head_object(Bucket=self._bucket, Key=key)
        except botocore.exceptions.ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                raise FileNotFoundError(key) from exc
            raise
        size = int(head.get("ContentLength", -1))
        metadata = head.get("Metadata", {})
        token = metadata.get("printstash-create-token")
        if size != expected_size or not isinstance(token, str) or not token:
            raise StorageCollisionError(key)
        get_kwargs: dict[str, str] = {"Bucket": self._bucket, "Key": key}
        version_id = head.get("VersionId")
        if version_id:
            get_kwargs["VersionId"] = str(version_id)
        response = self._client.get_object(**get_kwargs)
        digest = hashlib.sha256(response["Body"].read()).hexdigest()
        if digest != expected_sha256.lower():
            raise StorageCollisionError(key)
        etag = head.get("ETag")
        if etag and not str(etag).startswith('"'):
            etag = f'"{etag}"'
        receipt = CreationReceipt(
            key=key,
            size=size,
            token=token,
            backend="s3",
            namespace=f"{self._bucket}/{self._prefix()}",
            etag=str(etag) if etag else None,
            version_id=str(version_id) if version_id else None,
        )
        if not self.creation_matches(receipt):
            raise StorageCollisionError(key)
        return receipt

    def verify_destructive_access(self, keys: list[str]) -> None:
        if not keys:
            return
        probe_key = f"{self._prefix()}.printstash-delete-probes/{uuid.uuid4().hex}"
        receipt = self.create_bytes(b"", probe_key)
        if not self.rollback_create(receipt):
            raise RuntimeError("storage_delete_probe_cleanup_unverified")

    def move(self, src_key: str, dest_key: str) -> None:
        del src_key, dest_key
        raise RuntimeError("unchecked_storage_move_disabled")

    def stat_size(self, key: str) -> int:
        import botocore.exceptions

        self._validate_managed_key(key)
        try:
            resp = self._client.head_object(Bucket=self._bucket, Key=key)
        except botocore.exceptions.ClientError as exc:
            _raise_s3_missing_object(exc, key)
        return resp.get("ContentLength", 0)

    def read_bytes(self, key: str) -> bytes:
        self._validate_managed_key(key)
        resp = self._client.get_object(Bucket=self._bucket, Key=key)
        return resp["Body"].read()

    def stream_chunks(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        import botocore.exceptions

        self._validate_managed_key(key)
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
        except botocore.exceptions.ClientError as exc:
            _raise_s3_missing_object(exc, key)
        body = resp["Body"]
        try:
            while True:
                chunk = body.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            body.close()

    def download_to_path(self, key: str, dest: Path) -> Path:
        self._validate_managed_key(key)
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return _copy_stream_create_only(response["Body"], dest)

    def upload_file(self, src: Path, key: str) -> None:
        with src.open("rb") as source:
            self.create_stream(source, key)

    def ensure_setup(self) -> None:
        self._ensure_bucket()
        self._probe_capabilities()

    def delete(self, key: str) -> None:
        del key
        raise RuntimeError("unchecked_storage_delete_disabled")

    def list_keys(self, prefix: str = "") -> list[str]:
        full_prefix = self._validate_managed_prefix(prefix)
        keys: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def walk_keys(self, prefix: str = "") -> Iterator[str]:
        full_prefix = self._validate_managed_prefix(prefix)
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"]

    def usage(self, prefix: str = "") -> dict:
        full_prefix = self._validate_managed_prefix(prefix)
        total_size = 0
        object_count = 0
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                object_count += 1
                total_size += int(obj.get("Size", 0) or 0)
        return {
            "backend": "s3",
            "bucket": self._bucket,
            "prefix": full_prefix,
            "object_count": object_count,
            "total_size_bytes": total_size,
        }

    def presigned_download_url(self, key: str, filename: str) -> str | None:
        self._validate_managed_key(key)
        return self._client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{filename}"',
            },
            ExpiresIn=int(settings.s3_presigned_url_expire_seconds),
        )

    def health_probe(self) -> dict:
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return {
                "backend": "s3",
                "ok": True,
                "bucket": self._bucket,
                "endpoint": settings.s3_endpoint_url,
                "capabilities": self.capabilities.as_dict(),
                "diagnostics": self.probe_diagnostics,
            }
        except Exception as exc:
            return {
                "backend": "s3",
                "ok": False,
                "bucket": self._bucket,
                "endpoint": settings.s3_endpoint_url,
                "error": str(exc),
                "capabilities": self.capabilities.as_dict(),
                "diagnostics": self.probe_diagnostics,
            }


# ---------------------------------------------------------------------------
# Module-level backend singleton
# ---------------------------------------------------------------------------

_backend: StorageBackend | None = None


def get_backend() -> StorageBackend:
    """Return the backend selected by the application's composition root.

    Storage access is intentionally not constructed on demand here: doing so
    lets the first arbitrary caller choose process-wide infrastructure from
    mutable runtime settings.  Startup (or a test fixture) must construct,
    validate, and bind an adapter before any consumer accesses storage.
    """
    if _backend is None:
        raise RuntimeError(
            "storage_backend_not_bound: bind a configured backend before use"
        )
    return _backend


def create_backend(backend_name: str) -> StorageBackend:
    """Construct the adapter selected by *backend_name* without binding it.

    The caller owns setup validation and the lifetime of the process-wide
    binding.  Unknown values retain the historical local-storage fallback.
    """
    if backend_name == "s3":
        logger.info("constructing S3 storage backend (bucket=%s)", settings.s3_bucket)
        return S3StorageBackend()
    logger.info("constructing local storage backend")
    return LocalStorageBackend()


def bind_backend(backend: StorageBackend) -> StorageBackend:
    """Bind one already-configured backend for compatibility callers."""
    global _backend
    _backend = backend
    return backend


def init_backend() -> StorageBackend:
    """Legacy composition helper for tests and compatibility entrypoints.

    New application startup constructs the adapter explicitly, validates it,
    then calls :func:`bind_backend`.
    """
    backend = create_backend(settings.storage_backend)
    backend.ensure_setup()
    return bind_backend(backend)
