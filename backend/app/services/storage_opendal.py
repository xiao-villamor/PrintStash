"""Blocking remote-storage adapter for the supported OpenDAL transports."""

from __future__ import annotations

import asyncio
import hashlib
import mmap
import os
import posixpath
import tempfile
import uuid
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO, Iterator
from urllib.parse import quote, urlsplit, urlunsplit

from app.services.storage_backend import (
    CreationReceipt,
    ObjectIdentity,
    StorageBackend,
    StorageCapabilities,
    StorageCollisionError,
    StorageConfigurationError,
    StorageObjectInfo,
)
from app.services.storage_providers import TransportKind, TransportSpec


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


class OpenDALStorageBackend(StorageBackend):
    """Synchronous adapter for explicitly supported remote transports."""

    backend_name = "opendal"

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
        self._operator = operator if operator is not None else _operator_for(spec)
        self._read_only = False
        self._webdav_endpoint = str(spec.options.get("endpoint_url") or "").rstrip("/")
        self._webdav_root = str(spec.options.get("root") or "").strip("/")
        self._capabilities = StorageCapabilities(
            # WebDAV's conditional PUT and AsyncSSH's exclusive ``x`` mode are
            # atomic create-only operations.  They do not provide a durable
            # object identity/conditional replacement, so the adapter is
            # Guarded rather than Verified.
            # Do not advertise conditional publication until the configured
            # endpoint proves it.  A provider that silently overwrites a
            # duplicate key must remain unguarded/read-only.
            conditional_create=False,
            object_identity=ObjectIdentity.NONE,
            verified_delete=False,
            conditional_replace=False,
            namespace_ownership=True,
            direct_path=False,
        )
        self._probe_diagnostics: dict[str, object] = {
            "transport": spec.kind.value,
            "publication": "conditional_create",
            "verified_mutation": False,
        }

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

    def _webdav_move_create_only(self, temporary: str, destination: str) -> None:
        """Publish a staged object with WebDAV MOVE ``Overwrite: F``.

        OpenDAL 0.47 exposes common IO ``rename`` but not rename options, and
        its WebDAV implementation may overwrite a destination.  The protocol
        itself has the required atomic primitive, so issue that one request
        after OpenDAL has staged the bytes.  A server's 412 is a collision,
        never a generic publication failure.
        """
        import httpx

        options = self._spec.options
        response = httpx.request(
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
        import httpx

        parent = ""
        options = self._spec.options
        for part in relative.strip("/").split("/")[:-1]:
            parent = f"{parent}/{part}".strip("/")
            response = httpx.request(
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

    def blob_key(self, slug: str, version: int, filename: str) -> str:
        return self._key(f"files/{slug}/v{version}/{filename}")

    def thumbnail_key(self, file_id: int) -> str:
        return self._key(f"thumbs/{file_id}.webp")

    def legacy_thumbnail_key(self, file_id: int) -> str:
        return self._key(f"thumbs/{file_id}.png")

    def source_cover_key(self, provenance_source_id: int) -> str:
        return self._key(f"source-covers/{provenance_source_id}.webp")

    def capture_upload_slot_key(self, slot_id: str) -> str:
        return self._key(f"capture-slots/{slot_id}")

    def stl_cache_key(self, sha256: str) -> str:
        return self._key(f"cache/stl/{sha256}.stl")

    def collection_image_key(self, collection_id: int, name: str) -> str:
        return self._key(f"collection-images/{collection_id}/{name}")

    def document_file_key(self, document_id: int, name: str) -> str:
        return self._key(f"documents/{document_id}/{name}")

    def document_image_key(self, document_id: int, name: str) -> str:
        return self._key(f"document-images/{document_id}/{name}")

    def multipart_model_cover_key(self, multipart_model_id: int, name: str) -> str:
        return self._key(f"multipart-covers/{multipart_model_id}/{name}")

    def exists(self, key: str) -> bool:
        return bool(self._operator.exists(self._relative(key)))

    def create_stream(self, src: BinaryIO, key: str) -> CreationReceipt:
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
                        while chunk := src.read(1024 * 1024):
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
            elif self._spec.kind is TransportKind.WEBDAV and getattr(
                self._operator, "_printstash_test_double", False
            ):
                # Operator-only test doubles have no protocol endpoint.  The
                # production constructor always supplies one, so this branch
                # exists solely to exercise cleanup/error handling without
                # opening a socket.
                temporary = f".printstash-tmp-{uuid.uuid4().hex}"
                try:
                    with tempfile.TemporaryFile() as staged:
                        while chunk := src.read(1024 * 1024):
                            staged.write(chunk)
                        staged.seek(0)
                        self._operator.write(temporary, staged.read())
                    if self._operator.exists(destination):
                        raise StorageCollisionError(key)
                    self._operator.rename(temporary, destination)
                finally:
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
                self._operator.write_exclusive(destination, src)
            elif self._spec.kind in {TransportKind.S3, TransportKind.GDRIVE}:
                capabilities = self._operator.capability()
                create_only = bool(
                    getattr(capabilities, "write_with_if_not_exists", False)
                )
                if not create_only and self._operator.exists(destination):
                    raise StorageCollisionError(key)
                options = {"if_not_exists": True} if create_only else {}
                with self._operator.open(destination, "wb", **options) as writer:
                    while chunk := src.read(1024 * 1024):
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

    def move(self, src_key: str, dest_key: str) -> None:
        source = self._relative(src_key)
        destination = self._relative(dest_key)
        if self._spec.kind is TransportKind.WEBDAV and self._webdav_endpoint.startswith(
            ("http://", "https://")
        ):
            self._webdav_ensure_parent(destination)
            self._webdav_move_create_only(source, destination)
            return
        if self._spec.kind is TransportKind.SFTP:
            # SFTP rename is allowed to replace a destination.  The existence
            # check below would be a TOCTOU window, so do not expose it as a
            # create-only move until the server provides a no-replace rename.
            raise StorageConfigurationError("atomic_move_not_supported")
        if self._operator.exists(destination):
            raise StorageCollisionError(dest_key)
        self._operator.rename(source, destination)

    def stat_size(self, key: str) -> int:
        return int(self._operator.stat(self._relative(key)).content_length)

    def object_info(self, key: str) -> StorageObjectInfo | None:
        relative = self._relative(key)
        if not self._operator.exists(relative):
            return None
        metadata = self._operator.stat(relative)
        return StorageObjectInfo(
            size=int(metadata.content_length),
            etag=getattr(metadata, "etag", None),
            version_id=getattr(metadata, "version", None),
        )

    def read_bytes(self, key: str) -> bytes:
        return bytes(self._operator.read(self._relative(key)))

    @property
    def operator_capabilities(self):
        """Expose OpenDAL's measured operation surface to role-specific adapters."""
        return self._operator.capability()

    def check(self) -> None:
        self._operator.check()

    def open_reader(self, key: str):
        return self._operator.open(self._relative(key), "rb")

    def delete_versioned(self, key: str, version_id: str) -> None:
        if not getattr(self.operator_capabilities, "delete_with_version", False):
            raise StorageConfigurationError("conditional_delete_unavailable")
        self._operator.delete(self._relative(key), version=version_id)

    def stream_chunks(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        if hasattr(self._operator, "stream_chunks"):
            yield from self._operator.stream_chunks(self._relative(key), chunk_size)
            return
        with self._operator.open(self._relative(key), "rb") as reader:
            while chunk := reader.read(chunk_size):
                yield bytes(chunk)

    def download_to_path(self, key: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("xb") as output:
            for chunk in self.stream_chunks(key):
                output.write(chunk)
        return dest

    def upload_file(self, src: Path, key: str) -> None:
        with src.open("rb") as source:
            self.create_stream(source, key)

    def ensure_setup(self) -> None:
        self._operator.check()
        from io import BytesIO

        probe = f".printstash-probe/{uuid.uuid4().hex}"
        first = b"printstash-conditional-create-proof"
        second = b"printstash-conditional-create-collision"

        def cleanup() -> None:
            try:
                self._operator.delete(self._relative(self._key(probe)))
            except Exception:
                pass

        try:
            self.create_stream(BytesIO(first), self._key(probe))
        except Exception:
            self._probe_diagnostics["probed"] = True
            self._probe_diagnostics["conditional_create"] = False
            raise
        try:
            self.create_stream(BytesIO(second), self._key(probe))
        except StorageCollisionError:
            collision_proven = True
        except Exception:
            self._probe_diagnostics["probed"] = True
            self._probe_diagnostics["conditional_create"] = False
            cleanup()
            raise
        else:
            collision_proven = False
        try:
            observed = self.read_bytes(self._key(probe))
            observed_size = self.stat_size(self._key(probe))
            listed = self._key(probe) in set(self.walk_keys())
        except Exception:
            self._probe_diagnostics["probed"] = True
            self._probe_diagnostics["conditional_create"] = False
            cleanup()
            raise
        if not listed:
            cleanup()
            raise StorageConfigurationError("remote_conditional_create_unproven")
        if observed_size != len(first):
            if collision_proven:
                cleanup()
                raise StorageConfigurationError("remote_conditional_create_unproven")
            # Reached endpoint, but its duplicate write changed the object.
            cleanup()
            self._capabilities = StorageCapabilities(
                conditional_create=False,
                object_identity=ObjectIdentity.NONE,
                verified_delete=False,
                conditional_replace=False,
                namespace_ownership=True,
                direct_path=False,
            )
            self._probe_diagnostics.update(
                {"probed": True, "conditional_create": False, "read_only": True}
            )
            self._read_only = True
            return
        if observed != first:
            if collision_proven:
                cleanup()
                raise StorageConfigurationError("remote_conditional_create_unproven")
            cleanup()
            self._capabilities = StorageCapabilities(
                conditional_create=False,
                object_identity=ObjectIdentity.NONE,
                verified_delete=False,
                conditional_replace=False,
                namespace_ownership=True,
                direct_path=False,
            )
            self._probe_diagnostics.update(
                {"probed": True, "conditional_create": False, "read_only": True}
            )
            self._read_only = True
            return
        cleanup()
        self._capabilities = StorageCapabilities(
            conditional_create=True,
            object_identity=ObjectIdentity.NONE,
            verified_delete=False,
            conditional_replace=False,
            namespace_ownership=True,
            direct_path=False,
        )
        self._probe_diagnostics["probed"] = True
        self._probe_diagnostics["destructive_access"] = True
        self._probe_diagnostics["conditional_create"] = True

    def provision_root(self) -> None:
        """Create the configured SFTP root during an explicitly authorized setup.

        Startup and health checks intentionally call only ``check``.  A missing
        enrolled root therefore remains a fail-closed condition instead of being
        silently replaced by an empty directory.  The first-run setup wizard is
        the sole caller of this mutating seam.
        """
        if self._spec.kind is not TransportKind.SFTP:
            raise StorageConfigurationError("remote_root_provisioning_unsupported")
        provision = getattr(self._operator, "provision_root", None)
        if provision is None:
            raise StorageConfigurationError("sftp_root_provisioning_unavailable")
        provision()
        self._operator.check()

    def verify_destructive_access(self, keys: list[str]) -> None:
        """Probe create/delete on a fresh key, never on a caller's object."""
        del keys
        probe = f".printstash-probe/{uuid.uuid4().hex}"
        try:
            if self._spec.kind is TransportKind.SFTP:
                from io import BytesIO

                if not hasattr(self._operator, "write_exclusive"):
                    raise StorageConfigurationError("sftp_exclusive_create_unavailable")
                self._operator.write_exclusive(probe, BytesIO())
            elif self._spec.kind is TransportKind.WEBDAV:
                from io import BytesIO

                self.create_stream(BytesIO(), self._key(probe))
            self._operator.delete(probe)
        except Exception as exc:
            try:
                self._operator.delete(probe)
            except Exception:
                pass
            raise StorageConfigurationError(
                "remote_destructive_access_unavailable"
            ) from exc

    def delete(self, key: str) -> None:
        del key
        raise RuntimeError("unchecked_storage_delete_disabled")

    def list_keys(self, prefix: str = "") -> list[str]:
        return list(self.walk_keys(prefix))

    def list_prefix(self, prefix: str = "") -> list[str]:
        """Return full storage keys below a namespace-relative prefix."""
        return self.list_keys(prefix)

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

    def list_source_directory(
        self, relative: str, *, max_entries: int
    ) -> list[SourceDirectoryEntry]:
        """List one immediate directory with a hard response-size ceiling."""
        directory = relative.strip("/")
        # OpenDAL's WebDAV lister treats a non-root path without a trailing slash
        # as the collection object itself. A directory path must end in `/` to
        # return its immediate children; the SFTP adapter normalizes either form.
        listing_directory = f"{directory}/" if directory else ""
        iterator = iter(self._operator.list(listing_directory))
        raw_entries = list(islice(iterator, max_entries + 1))
        if len(raw_entries) > max_entries:
            raise StorageConfigurationError("remote_directory_entry_limit")
        entries: list[SourceDirectoryEntry] = []
        for entry in raw_entries:
            path = str(entry.path).strip("/")
            if not path or path == directory:
                continue
            metadata = entry.metadata
            entries.append(
                SourceDirectoryEntry(
                    key=path,
                    size=int(getattr(metadata, "content_length", 0) or 0),
                    is_dir=bool(getattr(metadata, "is_dir", False)),
                )
            )
        return entries

    def walk_keys(self, prefix: str = "") -> Iterator[str]:
        relative = self._relative(prefix) if prefix else ""
        for entry in self._operator.scan(relative):
            path = str(entry.path).rstrip("/")
            if path:
                yield self._key(path)

    def usage(self, prefix: str = "") -> dict:
        total = count = 0
        for key in self.walk_keys(prefix):
            info = self.object_info(key)
            if info is not None:
                count += 1
                total += info.size
        return {"bytes": total, "objects": count}

    def presigned_download_url(self, key: str, filename: str) -> str | None:
        del key, filename
        return None

    def health_probe(self) -> dict:
        try:
            self._operator.check()
            return {
                "backend": self.backend_name,
                "provider": self._spec.provider,
                "ok": True,
                "tier": self.capabilities.tier.value,
                "capabilities": self.capabilities.as_dict(),
                "warnings": list(self.capabilities.warnings),
                "diagnostics": self.probe_diagnostics,
            }
        except Exception as exc:
            return {
                "backend": self.backend_name,
                "provider": self._spec.provider,
                "ok": False,
                "error": exc.__class__.__name__,
                "tier": self.capabilities.tier.value,
                "capabilities": self.capabilities.as_dict(),
                "warnings": list(self.capabilities.warnings),
                "diagnostics": self.probe_diagnostics,
            }

    def direct_path(self, key: str) -> Path | None:
        self._relative(key)
        return None

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
        # Guarded transports expose no immutable object identity and no
        # conditional delete/quarantine primitive.  The proof above can be
        # invalidated by a replacement before delete, so retain the bytes and
        # let the ownership ledger record a durable blocked cleanup outcome.
        return False


def _operator_for(spec: TransportSpec):
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
        region = str(options.get("region") or "")
        if region and region != "auto":
            kwargs["region"] = region
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


@dataclass(frozen=True)
class SourceDirectoryEntry:
    """One immediate child below a read-only library source directory."""

    key: str
    size: int
    is_dir: bool


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
        return asyncio.run(self._perform(operation))

    def check(self) -> None:
        async def operation(client) -> None:
            # Health and startup checks are read-only. Creating a missing
            # remote root here can turn a typo or unmounted share into a new
            # empty namespace; provisioning belongs to explicit setup.
            await client.stat(self._root)

        self._run(operation)

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
        async def operation(client) -> int:
            attrs = await client.stat(self._path(relative))
            return int(attrs.size or 0)

        return _AsyncSSHMetadata(content_length=int(self._run(operation)))

    def read(self, relative: str) -> bytes:
        async def operation(client) -> bytes:
            reader = await client.open(self._path(relative), "rb")
            try:
                return bytes(await reader.read())
            finally:
                await reader.close()

        return bytes(self._run(operation))

    def stream_chunks(self, relative: str, chunk_size: int) -> Iterator[bytes]:
        import asyncssh

        loop = asyncio.new_event_loop()
        connection = loop.run_until_complete(
            asyncssh.connect(**self._connection_options())
        )
        client = loop.run_until_complete(connection.start_sftp_client())
        reader = loop.run_until_complete(client.open(self._path(relative), "rb"))
        try:
            while chunk := loop.run_until_complete(reader.read(chunk_size)):
                yield bytes(chunk)
        finally:
            loop.run_until_complete(reader.close())
            client.exit()
            loop.run_until_complete(client.wait_closed())
            connection.close()
            loop.run_until_complete(connection.wait_closed())
            loop.close()

    def delete(self, relative: str) -> None:
        async def operation(client) -> None:
            path = self._path(relative)
            if await client.exists(path):
                await client.remove(path)

        self._run(operation)

    def scan(self, relative: str):
        import asyncssh

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
                    if entry.attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY:
                        await walk(child)
                    else:
                        found.append(SimpleNamespace(path=child))

            await walk(relative)
            return found

        return self._run(operation)

    def list(self, relative: str):
        """List one directory only; recursive paging belongs to LibrarySource."""
        import asyncssh

        async def operation(client) -> list[SimpleNamespace]:
            directory = relative.strip("/")
            path = self._path(directory)
            if not await client.exists(path):
                return []
            found: list[SimpleNamespace] = []
            async for entry in client.scandir(path):
                name = str(entry.filename)
                if name in {"", ".", ".."}:
                    continue
                child = posixpath.join(directory, name).strip("/")
                is_dir = entry.attrs.type == asyncssh.FILEXFER_TYPE_DIRECTORY
                found.append(
                    SimpleNamespace(
                        path=child,
                        metadata=SimpleNamespace(
                            is_dir=is_dir,
                            content_length=int(entry.attrs.size or 0),
                        ),
                    )
                )
            return found

        return self._run(operation)
