"""Optional blocking WebDAV/SFTP storage adapter."""

from __future__ import annotations

import mmap
import tempfile
import uuid
from pathlib import Path
from typing import BinaryIO, Iterator

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
    if not opendal_available():
        return False
    if kind is TransportKind.WEBDAV:
        return True
    try:
        import opendal

        opendal.Operator("sftp")
    except Exception as exc:
        return "scheme is not registered" not in str(exc)
    return True


class OpenDALStorageBackend(StorageBackend):
    """Synchronous adapter for explicitly supported remote transports."""

    backend_name = "opendal"

    def __init__(self, spec: TransportSpec, *, operator=None) -> None:
        if spec.kind not in {TransportKind.WEBDAV, TransportKind.SFTP}:
            raise StorageConfigurationError("unsupported remote transport")
        self._spec = spec
        self.backend_name = spec.provider
        self._namespace = spec.namespace.rstrip("/")
        self._operator = operator if operator is not None else _operator_for(spec)
        self._capabilities = StorageCapabilities(
            conditional_create=False,
            object_identity=ObjectIdentity.NONE,
            verified_delete=False,
            conditional_replace=False,
            namespace_ownership=True,
            direct_path=False,
        )
        self._probe_diagnostics: dict[str, object] = {
            "transport": spec.kind.value,
            "publication": "temporary_key_then_rename",
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

    def exists(self, key: str) -> bool:
        return bool(self._operator.exists(self._relative(key)))

    def create_stream(self, src: BinaryIO, key: str) -> CreationReceipt:
        destination = self._relative(key)
        temporary = f".printstash-tmp/{uuid.uuid4().hex}"
        published = False
        try:
            if self._spec.kind is TransportKind.SFTP:
                with self._operator.open(temporary, "wb") as writer:
                    while chunk := src.read(1024 * 1024):
                        writer.write(chunk)
            else:
                # OpenDAL's WebDAV service exposes a one-shot writer. Spool to
                # disk and map the result so large models never occupy Python
                # heap memory during the single remote PUT.
                with tempfile.TemporaryFile() as staged:
                    while chunk := src.read(1024 * 1024):
                        staged.write(chunk)
                    size = staged.tell()
                    staged.flush()
                    if size:
                        mapped = mmap.mmap(staged.fileno(), 0, access=mmap.ACCESS_READ)
                        view = memoryview(mapped)
                        try:
                            self._operator.write(temporary, view)
                        finally:
                            view.release()
                            mapped.close()
                    else:
                        self._operator.write(temporary, b"")
            if self._operator.exists(destination):
                raise StorageCollisionError(key)
            self._operator.rename(temporary, destination)
            published = True
            metadata = self._operator.stat(destination)
            return CreationReceipt(
                key=key,
                size=int(metadata.content_length),
                token=uuid.uuid4().hex,
                backend=self.backend_name,
                namespace=self._namespace,
                etag=getattr(metadata, "etag", None),
            )
        finally:
            if not published:
                try:
                    self._operator.delete(temporary)
                except Exception:
                    pass

    def move(self, src_key: str, dest_key: str) -> None:
        source = self._relative(src_key)
        destination = self._relative(dest_key)
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
            size=int(metadata.content_length), etag=getattr(metadata, "etag", None)
        )

    def read_bytes(self, key: str) -> bytes:
        return bytes(self._operator.read(self._relative(key)))

    def stream_chunks(self, key: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
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
        self._probe_diagnostics["probed"] = True

    def delete(self, key: str) -> None:
        del key
        raise RuntimeError("unchecked_storage_delete_disabled")

    def list_keys(self, prefix: str = "") -> list[str]:
        return list(self.walk_keys(prefix))

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
    ) -> bool:
        info = self.object_info(key)
        if info is None:
            return True
        if info.size != expected_size:
            return False
        if expected_etag is not None and info.etag != expected_etag:
            return False
        self._operator.delete(self._relative(key))
        return True


def _operator_for(spec: TransportSpec):
    try:
        import opendal
    except ImportError as exc:
        raise StorageConfigurationError("Requires the full image") from exc

    options = spec.options
    if spec.kind is TransportKind.WEBDAV:
        return opendal.Operator(
            "webdav",
            endpoint=str(options["endpoint_url"]),
            root=str(options["root"]),
            username=str(options["username"]),
            password=str(options["password"]),
        )
    kwargs: dict[str, str] = {
        "endpoint": f"ssh://{options['host']}:{options['port']}",
        "root": str(options["root"]),
        "user": str(options["username"]),
        # Trust on first use: accept a previously unseen host, but let OpenSSH
        # reject a changed host key on later connections.
        "known_hosts_strategy": "Accept",
    }
    if "private_key_path" in options:
        kwargs["key"] = str(options["private_key_path"])
    if "password" in options:
        kwargs["password"] = str(options["password"])
    try:
        return opendal.Operator("sftp", **kwargs)
    except Exception as exc:
        raise StorageConfigurationError(
            "SFTP transport is unavailable in this full image"
        ) from exc
