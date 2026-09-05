"""Blocking remote-storage adapter for the supported OpenDAL transports."""

from __future__ import annotations

import asyncio
import mmap
import os
import posixpath
import tempfile
import uuid
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BufferedReader, RawIOBase
from itertools import islice
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Awaitable, BinaryIO, Iterator, TypeVar
from urllib.parse import quote, urlsplit, urlunsplit

from app.services.remote_deadline import operation_timeout
from app.services.remote_io import (
    IdentityDeletion,
    ManagedCreation,
    RemoteCapabilities,
    RemoteEntry,
    RemoteIO,
)
from app.services.storage_backend import (
    CreationReceipt,
    StorageCollisionError,
    StorageConfigurationError,
    StorageObjectInfo,
)
from app.services.storage_identity import StorageTargetIdentity, target_for_transport
from app.services.storage_providers import TransportKind, TransportSpec

_Result = TypeVar("_Result")


class _RemoteAdapter:
    """Shared key and transport mechanics, independent of managed Vault storage."""

    def __init__(self, spec: TransportSpec, *, operator=None) -> None:
        if spec.kind not in {
            TransportKind.S3,
            TransportKind.WEBDAV,
            TransportKind.SFTP,
            TransportKind.GDRIVE,
        }:
            raise StorageConfigurationError("unsupported remote transport")
        self._spec = spec
        self.backend_name = spec.provider
        self.provider_id = spec.provider
        self.transport = spec.kind.value
        self._namespace = spec.namespace.rstrip("/")
        # Optional native bindings have different Python signatures. Keep that
        # dynamic surface inside this implementation; callers use RemoteIO.
        self._operator: Any = operator if operator is not None else _operator_for(spec)
        self._read_only = False
        self._webdav_endpoint = str(spec.options.get("endpoint_url") or "").rstrip("/")
        self._webdav_root = str(spec.options.get("root") or "").strip("/")

    @property
    def _io_operator(self):
        timeout = operation_timeout()
        layer = getattr(self._operator, "layer", None)
        if layer is None or self._spec.kind is TransportKind.SFTP:
            return self._operator
        import opendal

        return layer(opendal.layers.TimeoutLayer(timeout=timeout, io_timeout=timeout))

    @property
    def storage_target(self) -> StorageTargetIdentity | None:
        return target_for_transport(self._spec.kind.value, self._spec.options)

    def _key(self, suffix: str) -> str:
        return f"{self._namespace}/{suffix.lstrip('/')}"

    def _relative(self, key: str) -> str:
        prefix = f"{self._namespace}/"
        if not key.startswith(prefix):
            raise ValueError("storage_key_outside_namespace")
        relative = key[len(prefix) :]
        if not relative or any(
            part in {"", ".", ".."} for part in Path(relative).parts
        ):
            raise ValueError("storage_key_invalid")
        return relative

    def _webdav_url(self, relative: str) -> str:
        """Build an authenticated WebDAV URL without treating keys as URLs."""
        if not self._webdav_endpoint:
            raise StorageConfigurationError("webdav_endpoint_required")
        parts = urlsplit(self._webdav_endpoint)
        path = "/".join(
            quote(part, safe="/")
            for part in (self._webdav_root, relative.strip("/"))
            if part
        )
        base = parts.path.rstrip("/")
        return urlunsplit(
            (parts.scheme, parts.netloc, f"{base}/{path}" or base or "/", "", "")
        )

    def _webdav_request(self, method: str, url: str, **kwargs):
        import httpx

        return httpx.request(method, url, **kwargs)

    def _webdav_move_create_only(self, temporary: str, destination: str) -> None:
        """Publish a staged object with WebDAV MOVE ``Overwrite: F``.

        OpenDAL 0.47 exposes common IO ``rename`` but not rename options, and
        its WebDAV implementation may overwrite a destination.  The protocol
        itself has the required atomic primitive, so issue that one request
        after OpenDAL has staged the bytes.  A server's 412 is a collision,
        never a generic publication failure.
        """

        options = self._spec.options
        response = self._webdav_request(
            "MOVE",
            self._webdav_url(temporary),
            headers={
                "Destination": self._webdav_url(destination),
                "Overwrite": "F",
            },
            auth=(
                str(options.get("username") or ""),
                str(options.get("password") or ""),
            ),
            timeout=60.0,
        )
        if response.status_code == 412:
            raise StorageCollisionError(destination)
        if response.status_code not in {201, 204}:
            # Nextcloud can answer 500 instead of the WebDAV-required 412 when
            # two Overwrite:F MOVE requests race. Do not broadly translate
            # server errors: only classify the failure as a collision after
            # confirming that the destination now exists.
            if self._operator.exists(destination):
                raise StorageCollisionError(destination)
            raise StorageConfigurationError(
                f"webdav_move_failed:{response.status_code}"
            )

    def _webdav_ensure_parent(self, relative: str) -> None:
        """Create destination collections before the atomic MOVE."""

        parent = ""
        options = self._spec.options
        for part in relative.strip("/").split("/")[:-1]:
            parent = f"{parent}/{part}".strip("/")
            response = self._webdav_request(
                "MKCOL",
                self._webdav_url(parent),
                auth=(
                    str(options.get("username") or ""),
                    str(options.get("password") or ""),
                ),
                timeout=60.0,
            )
            if response.status_code in {201, 405}:
                continue
            # WsgiDAV can answer 500 when two clients race MKCOL even though
            # the collection now exists. Confirm that state before proceeding.
            if response.status_code == 500 and self._operator.exists(parent):
                continue
            if response.status_code not in {201, 405}:
                raise StorageConfigurationError(
                    f"webdav_mkcol_failed:{response.status_code}"
                )

    def namespace_for(self, key: str) -> str:
        self._relative(key)
        return self._namespace

    def exists(self, key: str) -> bool:
        return bool(self._operator.exists(self._relative(key)))

    def publish_replica(self, source: BinaryIO, key: str) -> CreationReceipt:
        if self._read_only:
            raise StorageConfigurationError("remote_storage_read_only")
        destination = self._relative(key)
        try:
            if (
                self._spec.kind is TransportKind.WEBDAV
                and self._webdav_endpoint.startswith(("http://", "https://"))
            ):
                temporary = f".printstash-tmp-{uuid.uuid4().hex}"
                try:
                    with tempfile.TemporaryFile() as staged:
                        while chunk := source.read(1024 * 1024):
                            staged.write(chunk)
                        size = staged.tell()
                        staged.flush()
                        staged.seek(0)
                        if size:
                            mapped = mmap.mmap(
                                staged.fileno(), 0, access=mmap.ACCESS_READ
                            )
                            view = memoryview(mapped)
                            try:
                                self._operator.write(temporary, view)
                            finally:
                                view.release()
                                mapped.close()
                        else:
                            self._operator.write(temporary, b"")
                    self._webdav_ensure_parent(destination)
                    self._webdav_move_create_only(temporary, destination)
                finally:
                    # MOVE removes the source.  On every failed response this
                    # is the exact temporary key and cannot touch a caller key.
                    try:
                        self._operator.delete(temporary)
                    except Exception:
                        pass
            elif self._spec.kind is TransportKind.SFTP:
                # AsyncSSH maps ``x`` to O_EXCL on the server.  This is the
                # SFTP equivalent of WebDAV's If-None-Match: * and closes the
                # check-then-write race.
                if not hasattr(self._operator, "write_exclusive"):
                    raise StorageConfigurationError("sftp_exclusive_create_unavailable")
                self._operator.write_exclusive(destination, source)
            elif self._spec.kind is TransportKind.GDRIVE:
                # OpenDAL's Google Drive service uses a OneShotWriter: a second
                # ``writer.write`` fails even though the first chunk was
                # accepted. Stage to a temporary file, then hand the binding
                # one contiguous buffer without loading a large backup into
                # Python-managed memory.
                if self._operator.exists(destination):
                    raise StorageCollisionError(key)
                with tempfile.TemporaryFile() as staged:
                    while chunk := source.read(1024 * 1024):
                        staged.write(chunk)
                    size = staged.tell()
                    staged.flush()
                    if size:
                        mapped = mmap.mmap(staged.fileno(), 0, access=mmap.ACCESS_READ)
                        view = memoryview(mapped)
                        try:
                            self._operator.write(destination, view)
                        finally:
                            view.release()
                            mapped.close()
                    else:
                        self._operator.write(destination, b"")
            elif self._spec.kind is TransportKind.S3:
                capabilities = self._operator.capability()
                create_only = bool(
                    getattr(capabilities, "write_with_if_not_exists", False)
                )
                if not create_only and self._operator.exists(destination):
                    raise StorageCollisionError(key)
                options = {"if_not_exists": True} if create_only else {}
                with self._operator.open(destination, "wb", **options) as writer:
                    while chunk := source.read(1024 * 1024):
                        writer.write(chunk)
            else:
                raise StorageConfigurationError("webdav_protocol_endpoint_required")
        except Exception as exc:
            if _is_collision(exc):
                raise StorageCollisionError(key) from exc
            raise
        metadata = self._operator.stat(destination)
        return CreationReceipt(
            key=key,
            size=int(metadata.content_length),
            token=uuid.uuid4().hex,
            backend=self.backend_name,
            namespace=self._namespace,
            etag=getattr(metadata, "etag", None),
            version_id=getattr(metadata, "version", None),
        )

    def stat_size(self, key: str) -> int:
        try:
            return int(self._operator.stat(self._relative(key)).content_length)
        except Exception as exc:
            if _is_not_found(exc):
                raise FileNotFoundError(key) from exc
            raise

    def object_info(self, key: str) -> StorageObjectInfo | None:
        relative = self._relative(key)
        operator = self._io_operator
        try:
            if not operator.exists(relative):
                return None
            metadata = operator.stat(relative)
        except Exception as exc:
            if _is_not_found(exc):
                return None
            raise StorageConfigurationError("remote_storage_metadata_failed") from exc
        return StorageObjectInfo(
            size=int(metadata.content_length),
            etag=getattr(metadata, "etag", None),
            version_id=getattr(metadata, "version", None),
            modified_at=getattr(metadata, "last_modified", None),
        )

    def read_bytes(self, key: str) -> bytes:
        return bytes(self._operator.read(self._relative(key)))

    @property
    def operator_capabilities(self):
        """Supported transport operations; endpoint guarantees require a probe."""
        return self._operator.capability()

    def check(self) -> None:
        self._operator.check()

    def _delete_versioned(self, key: str, version_id: str) -> None:
        if (
            not version_id
            or version_id == "null"
            or not getattr(self.operator_capabilities, "delete_with_version", False)
        ):
            raise StorageConfigurationError("conditional_delete_unavailable")
        self._operator.delete(self._relative(key), version=version_id)

    @property
    def source_namespace(self) -> str:
        """Stable managed prefix used by the read-only LibrarySource adapter."""
        return self._namespace

    def source_key(self, relative: str) -> str:
        cleaned = relative.strip("/")
        return self._key(cleaned) if cleaned else self._namespace

    def source_relative_key(self, key: str) -> str:
        if key.rstrip("/") == self._namespace:
            return ""
        return self._relative(key.rstrip("/"))

    @property
    def operations(self) -> RemoteCapabilities:
        raw = self.operator_capabilities
        conditional = self._spec.kind is TransportKind.WEBDAV or bool(
            getattr(raw, "write_with_if_not_exists", False)
        )
        return RemoteCapabilities(
            read=bool(getattr(raw, "read", False)),
            write=bool(getattr(raw, "write", False)),
            list=bool(getattr(raw, "list", False)),
            conditional_create=conditional
            and self._spec.kind is not TransportKind.GDRIVE,
            atomic_visibility=conditional
            and self._spec.kind in {TransportKind.S3, TransportKind.WEBDAV},
            versioned_delete=bool(getattr(raw, "delete_with_version", False)),
        )

    @property
    def managed_creation(self) -> ManagedCreation | None:
        if not self.operations.conditional_create:
            return None
        return _ManagedCreation(self)

    @property
    def exact_deletion(self) -> IdentityDeletion | None:
        return _IdentityDeletion(self) if self.operations.versioned_delete else None

    @contextmanager
    def open_reader(
        self, key: str, *, expected: StorageObjectInfo | None = None
    ) -> Iterator[BinaryIO]:
        options: dict[str, object] = {}
        if expected is not None:
            raw = self.operator_capabilities
            if (
                expected.version_id
                and expected.version_id != "null"
                and getattr(raw, "read_with_version", False)
            ):
                options["version"] = expected.version_id
            elif expected.etag and getattr(raw, "read_with_if_match", False):
                options["if_match"] = expected.etag
            elif expected.modified_at is not None and getattr(
                raw, "read_with_if_unmodified_since", False
            ):
                options["if_unmodified_since"] = expected.modified_at
        with ExitStack() as resources:
            try:
                reader = resources.enter_context(
                    self._io_operator.open(self._relative(key), "rb", **options)
                )
            except StorageConfigurationError:
                raise
            except Exception as exc:
                if _is_not_found(exc):
                    raise FileNotFoundError(key) from exc
                raise StorageConfigurationError("remote_storage_read_failed") from exc
            normalized = resources.enter_context(BufferedReader(_RemoteReader(reader)))
            yield normalized

    def stream_chunks(
        self,
        key: str,
        chunk_size: int = 1024 * 1024,
        *,
        expected: StorageObjectInfo | None = None,
    ) -> Iterator[bytes]:
        with self.open_reader(key, expected=expected) as reader:
            while chunk := reader.read(chunk_size):
                yield bytes(chunk)

    def _webdav_listing(self, directory: str):
        from app.services.webdav_listing import iter_webdav_directory

        return iter_webdav_directory(
            self._webdav_url(directory),
            root_url=self._webdav_url(""),
            username=str(self._spec.options.get("username") or ""),
            password=str(self._spec.options.get("password") or ""),
        )

    @contextmanager
    def iter_directory(self, relative: str) -> Iterator[Iterator[RemoteEntry]]:
        directory = relative.strip("/")

        def observations():
            listing = None
            try:
                if self._spec.kind is TransportKind.WEBDAV:
                    with self._webdav_listing(directory) as entries:
                        for entry in entries:
                            if entry.key != directory:
                                yield entry
                    return
                listing = iter(
                    self._io_operator.list(f"{directory}/" if directory else "")
                )
                while True:
                    operation_timeout()
                    try:
                        entry = next(listing)
                    except StopIteration:
                        break
                    path = str(entry.path).strip("/")
                    if not path or path == directory:
                        continue
                    metadata = entry.metadata
                    yield RemoteEntry(
                        key=path,
                        size=int(getattr(metadata, "content_length", 0) or 0),
                        is_dir=bool(getattr(metadata, "is_dir", False)),
                        modified_at=getattr(metadata, "last_modified", None),
                        etag=getattr(metadata, "etag", None) or None,
                        version_id=getattr(metadata, "version", None) or None,
                    )
            except StorageConfigurationError:
                raise
            except Exception as exc:
                operation_timeout()
                raise StorageConfigurationError("remote_storage_list_failed") from exc
            finally:
                close = getattr(listing, "close", None)
                if close is not None:
                    close()

        with closing(observations()) as entries:
            yield entries

    def list_source_directory(
        self, relative: str, *, max_entries: int
    ) -> list[RemoteEntry]:
        with self.iter_directory(relative) as entries:
            page = list(islice(entries, max_entries + 1))
        if len(page) > max_entries:
            raise StorageConfigurationError("remote_directory_entry_limit")
        return page

    def walk_keys(self, prefix: str = "") -> Iterator[str]:
        directory = self._relative(prefix).rstrip("/") if prefix else ""
        with self.iter_directory(directory) as entries:
            for entry in entries:
                if entry.is_dir:
                    yield from self.walk_keys(self.source_key(entry.key))
                else:
                    yield self.source_key(entry.key)


class _RemoteReader(RawIOBase):
    """Normalize transport reads without intercepting exceptions from the consumer."""

    def __init__(self, reader) -> None:
        super().__init__()
        self.reader = reader

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        operation_timeout()
        try:
            read = getattr(self.reader, "read1", self.reader.read)
            chunk = read(len(buffer))
        except Exception as exc:
            raise StorageConfigurationError("remote_storage_read_failed") from exc
        buffer[: len(chunk)] = chunk
        return len(chunk)


@dataclass(frozen=True)
class _ManagedCreation:
    remote: _RemoteAdapter

    @property
    def atomic_visibility(self) -> bool:
        return self.remote.operations.atomic_visibility

    def create_stream(self, source: BinaryIO, key: str) -> CreationReceipt:
        return self.remote.publish_replica(source, key)


@dataclass(frozen=True)
class _IdentityDeletion:
    remote: _RemoteAdapter

    def delete_versioned(self, key: str, version_id: str) -> None:
        self.remote._delete_versioned(key, version_id)


class OpenDALRemoteIO(_RemoteAdapter):
    """OpenDAL remote bytes; no implicit managed-storage contract."""

    def __init__(self, spec: TransportSpec, *, operator=None) -> None:
        if spec.kind is TransportKind.SFTP:
            raise StorageConfigurationError("sftp_requires_asyncssh")
        super().__init__(spec, operator=operator)


class AsyncSSHRemoteIO(_RemoteAdapter):
    """Incremental SFTP operations with explicit host verification."""

    def __init__(self, spec: TransportSpec, *, operator=None) -> None:
        if spec.kind is not TransportKind.SFTP:
            raise StorageConfigurationError("asyncssh_requires_sftp")
        super().__init__(spec, operator=operator)


def remote_io_for(spec: TransportSpec, *, operator=None) -> RemoteIO:
    adapter = AsyncSSHRemoteIO if spec.kind is TransportKind.SFTP else OpenDALRemoteIO
    return adapter(spec, operator=operator)


def opendal_available() -> bool:
    try:
        import opendal  # noqa: F401

        return True
    except ImportError:
        return False


def opendal_transport_available(kind: TransportKind) -> bool:
    if kind is TransportKind.SFTP:
        try:
            import asyncssh  # noqa: F401
        except ImportError:
            return False
        return True
    return opendal_available()


def _is_collision(exc: Exception) -> bool:
    """Recognize protocol precondition failures without masking transport errors."""
    text = str(exc).lower()
    return isinstance(exc, FileExistsError) or any(
        marker in text
        for marker in ("412", "precondition", "already exists", "file exists")
    )


def _is_not_found(exc: Exception) -> bool:
    """Normalize provider-specific absence into the StorageBackend contract."""
    text = str(exc).lower()
    return (
        isinstance(exc, (FileNotFoundError, KeyError))
        or exc.__class__.__name__ == "NotFound"
        or "404 not found" in text
    )


def _operator_for(spec: TransportSpec) -> Any:
    options = spec.options
    if spec.kind is TransportKind.S3:
        try:
            import opendal
        except ImportError as exc:
            raise StorageConfigurationError("Requires the full image") from exc
        kwargs = {
            "bucket": str(options["bucket"]),
            "root": str(options["root"]),
            "access_key_id": str(options["access_key"]),
            "secret_access_key": str(options["secret_key"]),
            "disable_config_load": "true",
            "disable_ec2_metadata": "true",
        }
        addressing_style = str(options.get("addressing_style") or "auto")
        if addressing_style != "auto":
            kwargs["enable_virtual_host_style"] = str(
                addressing_style == "virtual"
            ).lower()
        endpoint = str(options.get("endpoint_url") or "")
        if endpoint:
            kwargs["endpoint"] = endpoint
        # The pinned Python binding requires an explicit signing region. R2's
        # literal "auto" is a valid region, not a request for network discovery.
        kwargs["region"] = str(options.get("region") or "auto")
        return opendal.Operator("s3", **kwargs)
    if spec.kind is TransportKind.WEBDAV:
        try:
            import opendal
        except ImportError as exc:
            raise StorageConfigurationError("Requires the full image") from exc
        return opendal.Operator(
            "webdav",
            endpoint=str(options["endpoint_url"]),
            root=str(options["root"]),
            username=str(options["username"]),
            password=str(options["password"]),
        )
    if spec.kind is TransportKind.GDRIVE:
        try:
            import opendal
        except ImportError as exc:
            raise StorageConfigurationError("Requires the full image") from exc
        try:
            return opendal.Operator(
                "gdrive",
                root=str(options["root"]),
                client_id=str(options["client_id"]),
                client_secret=str(options["client_secret"]),
                refresh_token=str(options["refresh_token"]),
            )
        except opendal.exceptions.Unsupported as exc:
            raise StorageConfigurationError("gdrive_transport_unavailable") from exc
    if spec.kind is TransportKind.SFTP:
        # The OpenDAL SFTP service only accepts a strategy (and uses the
        # process-wide OpenSSH catalogue), while PrintStash accepts a mounted
        # file or explicit known-host entry.  AsyncSSH gives both auth modes
        # the same explicit verification contract and exclusive-create seam.
        if not str(options.get("host_key") or "").strip():
            raise StorageConfigurationError("sftp_host_key_required")
        return _AsyncSSHSFTPOperator(options)
    raise StorageConfigurationError("unsupported remote transport")


@dataclass(frozen=True)
class _AsyncSSHMetadata:
    content_length: int
    etag: None = None
    last_modified: datetime | None = None


@dataclass(frozen=True)
class _SFTPCapabilities:
    """Supported operations; root access is probed separately.

    Exclusive creation prevents overwrite but does not promise atomic visible
    publication or an immutable version identity for deletion.
    """

    read: bool = True
    write: bool = True
    list: bool = True
    write_with_if_not_exists: bool = True
    delete_with_version: bool = False


class _ChunkReader(RawIOBase):
    """Adapt a bounded transport iterator to Python's streaming file contract."""

    def __init__(self, chunks: Iterator[bytes]) -> None:
        super().__init__()
        self._chunks = chunks
        self._pending = memoryview(b"")

    def readable(self) -> bool:
        return True

    def readinto(self, buffer) -> int:
        if self.closed:
            raise ValueError("read of closed remote stream")
        if not buffer:
            return 0
        if not self._pending:
            try:
                self._pending = memoryview(next(self._chunks))
            except StopIteration:
                return 0
            except Exception as exc:
                raise StorageConfigurationError("remote_storage_read_failed") from exc
        count = min(len(buffer), len(self._pending))
        buffer[:count] = self._pending[:count]
        self._pending = self._pending[count:]
        return count

    def close(self) -> None:
        try:
            close = getattr(self._chunks, "close", None)
            if close is not None:
                close()
        except Exception as exc:
            raise StorageConfigurationError("remote_storage_read_failed") from exc
        finally:
            self._pending = memoryview(b"")
            super().close()


def _sftp_modified_at(attrs) -> datetime | None:
    mtime = getattr(attrs, "mtime", None)
    if mtime is None:
        return None
    seconds = mtime + (getattr(attrs, "mtime_ns", None) or 0) / 1_000_000_000
    return datetime.fromtimestamp(seconds, timezone.utc)


class _AsyncSSHSFTPOperator:
    """Synchronous, pinned-host SFTP operator with exclusive creation.

    AsyncSSH owns every SFTP authentication mode so password and mounted-key
    setups share the same host-verification and server-side ``O_EXCL`` contract.
    There is no OpenDAL/OpenSSH fallback with different trust behavior.
    """

    def __init__(self, options: dict[str, str | int | bool]) -> None:
        try:
            import asyncssh  # noqa: F401
        except ImportError as exc:
            raise StorageConfigurationError("Requires the full image") from exc
        self._host = str(options["host"])
        self._port = int(options["port"])
        self._username = str(options["username"])
        self._password = str(options.get("password") or "")
        self._key_path = str(options.get("private_key_path") or "")
        self._passphrase = str(options.get("passphrase") or "")
        self._host_key = str(options.get("host_key") or "").strip()
        self._root = str(options["root"]).strip("/")

    def _known_hosts(self):
        if not self._host_key:
            raise StorageConfigurationError("sftp_host_key_required")
        if os.path.isfile(self._host_key):
            return self._host_key
        import asyncssh

        try:
            return asyncssh.import_known_hosts(self._host_key)
        except Exception as exc:
            raise StorageConfigurationError("sftp_host_key_invalid") from exc

    def _connection_options(self) -> dict[str, object]:
        options: dict[str, object] = {
            "host": self._host,
            "port": self._port,
            "username": self._username,
            "known_hosts": self._known_hosts(),
        }
        if self._password:
            options["password"] = self._password
            options["client_keys"] = None
        else:
            options["client_keys"] = [self._key_path]
            if self._passphrase:
                options["passphrase"] = self._passphrase
        return options

    def _path(self, relative: str) -> str:
        cleaned = relative.strip("/")
        return posixpath.join(self._root, cleaned) if cleaned else self._root

    async def _perform(self, operation):
        import asyncssh

        async with asyncssh.connect(**self._connection_options()) as connection:
            async with connection.start_sftp_client() as client:
                return await operation(client)

    def _run(self, operation):
        return asyncio.run(
            asyncio.wait_for(self._perform(operation), operation_timeout())
        )

    def check(self) -> None:
        async def operation(client) -> None:
            # Health and startup checks are read-only. Creating a missing
            # remote root here can turn a typo or unmounted share into a new
            # empty namespace; provisioning belongs to explicit setup.
            await client.stat(self._root)

        self._run(operation)

    def capability(self) -> _SFTPCapabilities:
        return _SFTPCapabilities()

    def open(self, relative: str, mode: str = "rb") -> BufferedReader:
        if mode != "rb":
            raise StorageConfigurationError("sftp_stream_mode_unsupported")
        return BufferedReader(_ChunkReader(self.stream_chunks(relative, 64 * 1024)))

    def provision_root(self) -> None:
        async def operation(client) -> None:
            # This method is intentionally separate from ``check``.  Only an
            # explicit setup flow may create a missing enrolled root.
            await client.makedirs(self._root, exist_ok=True)

        self._run(operation)

    def exists(self, relative: str) -> bool:
        async def operation(client) -> bool:
            return bool(await client.exists(self._path(relative)))

        return bool(self._run(operation))

    def write_stream(self, relative: str, source: BinaryIO) -> None:
        async def operation(client) -> None:
            # Never recreate an enrolled root after a mount disappears.  Only
            # the explicit first-run ``provision_root`` seam may create it.
            path = self._path(relative)
            await self._ensure_parent(client, path)
            writer = await client.open(path, "wb")
            try:
                while chunk := source.read(1024 * 1024):
                    await writer.write(chunk)
            finally:
                await writer.close()

        self._run(operation)

    def write_exclusive(self, relative: str, source: BinaryIO) -> None:
        """Write using SFTP's server-side O_EXCL (AsyncSSH ``x`` mode)."""
        import asyncssh

        async def operation(client) -> None:
            # See ``write_stream``: a runtime write must fail closed when the
            # enrolled root is no longer mounted, rather than recreating a
            # new empty directory in the wrong filesystem.
            path = self._path(relative)
            await self._ensure_parent(client, path)
            try:
                writer = await client.open(path, "xb")
            except asyncssh.SFTPFailure as exc:
                # Some OpenSSH/SFTP servers report O_EXCL collisions as the
                # generic SFTP_FAILURE status. Confirm the destination after
                # that failure before translating it; an unrelated server or
                # transport error must remain visible and no overwrite is
                # ever attempted.
                try:
                    destination_exists = await client.exists(path)
                except Exception:
                    raise
                if destination_exists:
                    raise StorageCollisionError(relative) from exc
                raise
            try:
                while chunk := source.read(1024 * 1024):
                    await writer.write(chunk)
            finally:
                await writer.close()

        self._run(operation)

    def write(self, relative: str, data: bytes) -> None:
        from io import BytesIO

        self.write_stream(relative, BytesIO(data))

    async def _ensure_parent(self, client, path: str) -> None:
        """Create descendants without recursively creating the configured root.

        SFTP ``makedirs(root/child, exist_ok=True)`` is recursive. If the
        configured root is a mount that disappears after a preflight ``stat``,
        that call can silently recreate the root on the server's parent
        filesystem. Walk each component instead: the root is checked first,
        and each missing child is created with a single-level ``mkdir``. A
        root loss between those operations therefore makes the child mkdir fail
        closed rather than reconstructing the root.
        """
        import asyncssh

        await client.stat(self._root)
        parent = posixpath.dirname(path)
        if not parent or parent == self._root:
            return
        relative_parent = posixpath.relpath(parent, self._root)
        if relative_parent in {"", "."}:
            return
        current = self._root
        for component in relative_parent.split("/"):
            if component in {"", ".", ".."}:
                raise StorageConfigurationError("sftp_path_outside_root")
            current = posixpath.join(current, component)
            try:
                await client.stat(current)
            except asyncssh.SFTPNoSuchFile:
                await client.mkdir(current)

    def rename(self, source: str, destination: str) -> None:
        async def operation(client) -> None:
            target = self._path(destination)
            await self._ensure_parent(client, target)
            await client.rename(self._path(source), target)

        self._run(operation)

    def stat(self, relative: str) -> _AsyncSSHMetadata:
        async def operation(client) -> _AsyncSSHMetadata:
            attrs = await client.stat(self._path(relative))
            return _AsyncSSHMetadata(
                content_length=int(attrs.size or 0),
                last_modified=_sftp_modified_at(attrs),
            )

        return self._run(operation)

    def read(self, relative: str) -> bytes:
        async def operation(client) -> bytes:
            reader = await client.open(self._path(relative), "rb")
            try:
                return bytes(await reader.read())
            finally:
                await reader.close()

        return bytes(self._run(operation))

    def _await(
        self,
        loop: asyncio.AbstractEventLoop,
        operation: Awaitable[_Result],
        connection=None,
    ) -> _Result:
        try:
            timeout = operation_timeout()
        except BaseException:
            if asyncio.iscoroutine(operation):
                operation.close()
            if connection is not None:
                connection.abort()
            raise
        # AsyncSSH iterator cancellation can await a remote CLOSE response.
        # Abort at the same deadline so cancellation cannot wait on that server.
        abort_timer = (
            loop.call_later(timeout, connection.abort)
            if connection is not None
            else None
        )
        try:
            return loop.run_until_complete(asyncio.wait_for(operation, timeout))
        finally:
            if abort_timer is not None:
                abort_timer.cancel()

    def stream_chunks(self, relative: str, chunk_size: int) -> Iterator[bytes]:
        import asyncssh

        # Register each resource as soon as acquisition succeeds. ExitStack
        # runs the remaining cleanups even if closing an inner resource fails.
        # Acquisition failures and GeneratorExit own the loop just as EOF does.
        with ExitStack() as resources:
            loop = asyncio.new_event_loop()
            resources.callback(loop.close)
            connection = self._await(
                loop, asyncssh.connect(**self._connection_options())
            )
            resources.callback(
                lambda: loop.run_until_complete(
                    asyncio.wait_for(connection.wait_closed(), 5)
                )
            )
            resources.callback(connection.close)
            client = self._await(loop, connection.start_sftp_client(), connection)
            resources.callback(
                lambda: loop.run_until_complete(
                    asyncio.wait_for(client.wait_closed(), 5)
                )
            )
            resources.callback(client.exit)
            reader = self._await(
                loop, client.open(self._path(relative), "rb"), connection
            )
            resources.callback(
                lambda: loop.run_until_complete(asyncio.wait_for(reader.close(), 5))
            )
            while chunk := self._await(loop, reader.read(chunk_size), connection):
                yield bytes(chunk)

    def delete(self, relative: str) -> None:
        async def operation(client) -> None:
            path = self._path(relative)
            if await client.exists(path):
                await client.remove(path)

        self._run(operation)

    def scan(self, relative: str):
        from asyncssh.constants import FILEXFER_TYPE_DIRECTORY

        async def operation(client) -> list[SimpleNamespace]:
            found: list[SimpleNamespace] = []
            visited: set[str] = set()

            async def walk(directory: str) -> None:
                normalized = directory.strip("/")
                if normalized in visited:
                    return
                visited.add(normalized)
                path = self._path(directory)
                if not await client.exists(path):
                    return
                async for entry in client.scandir(path):
                    name = str(entry.filename)
                    if name in {"", ".", ".."}:
                        continue
                    child = posixpath.join(directory.strip("/"), name).strip("/")
                    if child == normalized:
                        continue
                    if entry.attrs.type == FILEXFER_TYPE_DIRECTORY:
                        await walk(child)
                    else:
                        found.append(SimpleNamespace(path=child))

            await walk(relative)
            return found

        return self._run(operation)

    def list(self, relative: str):
        """Yield one directory incrementally; early close releases its connection."""
        import asyncssh
        from asyncssh.constants import FILEXFER_TYPE_DIRECTORY

        directory = relative.strip("/")
        with ExitStack() as resources:
            loop = asyncio.new_event_loop()
            resources.callback(loop.close)
            connection = self._await(
                loop, asyncssh.connect(**self._connection_options())
            )
            resources.callback(
                lambda: loop.run_until_complete(
                    asyncio.wait_for(connection.wait_closed(), 5)
                )
            )
            resources.callback(connection.close)
            client = self._await(loop, connection.start_sftp_client(), connection)
            resources.callback(
                lambda: loop.run_until_complete(
                    asyncio.wait_for(client.wait_closed(), 5)
                )
            )
            resources.callback(client.exit)
            iterator = client.scandir(self._path(directory)).__aiter__()
            close_listing = getattr(iterator, "aclose", None)
            if close_listing is not None:
                resources.callback(
                    lambda: loop.run_until_complete(
                        asyncio.wait_for(close_listing(), 5)
                    )
                )
            while True:
                try:
                    entry = self._await(loop, anext(iterator), connection)
                except StopAsyncIteration:
                    break
                name = str(entry.filename)
                if name in {"", ".", ".."}:
                    continue
                yield SimpleNamespace(
                    path=posixpath.join(directory, name).strip("/"),
                    metadata=SimpleNamespace(
                        is_dir=entry.attrs.type == FILEXFER_TYPE_DIRECTORY,
                        content_length=int(entry.attrs.size or 0),
                        last_modified=_sftp_modified_at(entry.attrs),
                    ),
                )
