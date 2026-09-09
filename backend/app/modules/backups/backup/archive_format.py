"""Persisted archive format parsing and validation."""

from __future__ import annotations

import gzip
import hashlib
import json
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

import app.modules.backups.backup.contracts as _contracts_module
from app.core.logging import get_logger
from app.modules.storage.storage_backend.runtime import get_backend

logger = get_logger(__name__)


def _read_manifest(archive_path: Path) -> _contracts_module.BackupMeta | None:
    with gzip.open(archive_path, "rb") as gz:
        with tarfile.open(fileobj=gz, mode="r|") as tar:
            for member in tar:
                if member.name == "manifest.json":
                    f = tar.extractfile(member)
                    if f is None:
                        return None
                    manifest = json.loads(f.read().decode("utf-8"))
                    return _contracts_module.BackupMeta(
                        # ``.stem`` only strips ``.gz`` from ``*.tar.gz`` and
                        # would leave a ``.tar`` suffix on the id, so the id here
                        # would not match the one ``create_backup`` returns.
                        id=archive_path.name.removesuffix(".tar.gz").rsplit("-", 1)[-1],
                        created_at=manifest["created_at"],
                        size_bytes=archive_path.stat().st_size,
                        storage_backend=manifest.get("storage_backend", "local"),
                        file_count=manifest.get("file_count", 0),
                        app_version=manifest.get("app_version", "unknown"),
                        path=str(archive_path),
                        location="local",
                    )
    return None


def _read_manifest_from_stream(body: Any) -> _contracts_module.BackupMeta | None:
    """Read the first manifest from an S3 body without draining the archive.

    Backup creation writes ``manifest.json`` as the first tar member.  The
    stream mode therefore lets listing release the response after a small,
    bounded read instead of downloading and validating the entire archive.
    Full member/hash/SQLite validation belongs to explicit verification and
    recovery paths.
    """
    with gzip.GzipFile(fileobj=body, mode="rb") as gz:
        with tarfile.open(fileobj=gz, mode="r|") as tar:
            first = next(iter(tar), None)
            if first is None or first.name != "manifest.json" or not first.isfile():
                return None
            stream = tar.extractfile(first)
            if stream is None:
                return None
            try:
                parsed = json.loads(stream.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return None
            if not isinstance(parsed, dict):
                return None
            return _contracts_module.BackupMeta(
                id="",
                created_at=str(parsed["created_at"]),
                size_bytes=0,
                storage_backend=str(parsed.get("storage_backend", "local")),
                file_count=int(parsed.get("file_count", 0)),
                app_version=str(parsed.get("app_version", "unknown")),
                path="",
                location="s3",
            )


def _unsafe_member_name(name: str) -> bool:
    path = PurePosixPath(name)
    return path.is_absolute() or ".." in path.parts or not name or "\\" in name


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_key(key: str) -> str:
    digest = hashlib.sha256()
    for chunk in get_backend().stream_chunks(key):
        digest.update(chunk)
    return digest.hexdigest()


def _has_member(tar: tarfile.TarFile, name: str) -> bool:
    try:
        tar.getmember(name)
        return True
    except KeyError:
        return False


def _restore_key_map(tar: tarfile.TarFile) -> dict[str, str]:
    """Return the arcname → original storage key map from the archive manifest.

    Empty for legacy archives that predate the map; callers fall back to the
    arcname transform in that case.
    """
    if not _has_member(tar, "manifest.json"):
        return {}
    f = tar.extractfile("manifest.json")
    if f is None:
        return {}
    try:
        manifest = json.loads(f.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    result: dict[str, str] = {}
    for entry in manifest.get("files", []):
        if not isinstance(entry, dict):
            continue
        member = entry.get("member", entry.get("arc"))
        key = entry.get("key")
        if isinstance(member, str) and isinstance(key, str):
            result[member] = key
    return result


def read_archive_manifest(archive_path: Path) -> dict:
    """Read a validated restore manifest for recovery-prerequisite consumers."""
    with tarfile.open(archive_path, "r:gz") as archive:
        manifest, _entries = _restore_manifest_entries(archive)
    return manifest


def _restore_manifest_entries(tar: tarfile.TarFile) -> tuple[dict, dict[str, dict]]:
    """Read and validate manifest metadata before any destination is written."""
    if not _has_member(tar, "manifest.json"):
        raise RuntimeError("backup_manifest_invalid")
    source = tar.extractfile("manifest.json")
    if source is None:
        raise RuntimeError("backup_manifest_invalid")
    try:
        manifest = json.loads(source.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError("backup_manifest_invalid") from exc
    if (
        not isinstance(manifest, dict)
        or str(manifest.get("version"))
        not in _contracts_module._SUPPORTED_MANIFEST_VERSIONS
    ):
        raise RuntimeError("backup_manifest_invalid")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise RuntimeError("backup_manifest_invalid")

    version = str(manifest["version"])
    if version == _contracts_module.MANIFEST_VERSION and (
        not isinstance(manifest.get("provider_id"), str)
        or not isinstance(manifest.get("transport"), str)
        or not isinstance(manifest.get("namespace"), (str, type(None)))
        or not isinstance(manifest.get("namespaces"), list)
        or any(not isinstance(value, str) for value in manifest["namespaces"])
    ):
        raise RuntimeError("backup_manifest_invalid")
    entries: dict[str, dict] = {}
    keys: set[str] = set()
    backend = get_backend()
    for entry in files:
        if not isinstance(entry, dict):
            raise RuntimeError("backup_manifest_invalid")
        member = entry.get("member", entry.get("arc"))
        key = entry.get("key")
        if not isinstance(member, str) or not isinstance(key, str):
            raise RuntimeError("backup_manifest_invalid")
        if (
            member in entries
            or key in keys
            or _unsafe_member_name(member)
            or not member.startswith("files/")
        ):
            raise RuntimeError("backup_manifest_invalid")
        _validate_restore_key(key)
        if version in {
            _contracts_module._LEGACY_MANIFEST_V2,
            _contracts_module.MANIFEST_VERSION,
        }:
            provider_id = str(getattr(backend, "provider_id", backend.backend_name))
            transport = str(getattr(backend, "transport", backend.backend_name))
            sha256 = entry.get("sha256")
            if (
                entry.get("namespace") != backend.namespace_for(key)
                or not isinstance(entry.get("size"), int)
                or entry["size"] < 0
                or not isinstance(sha256, str)
                or len(sha256) != 64
                or any(character not in "0123456789abcdef" for character in sha256)
                or (
                    version == _contracts_module._LEGACY_MANIFEST_V2
                    and entry.get("provider") != backend.backend_name
                )
                or (
                    version == _contracts_module.MANIFEST_VERSION
                    and (
                        entry.get("provider_id") != provider_id
                        or entry.get("transport") != transport
                        # v3 keeps the v2 ``provider`` field for old readers.
                        # When present it is still identity evidence, so a
                        # foreign value must not be silently ignored.
                        or (
                            entry.get("provider") is not None
                            and entry.get("provider") != backend.backend_name
                        )
                    )
                )
            ):
                raise RuntimeError("backup_storage_namespace_mismatch")
        entries[member] = entry
        keys.add(key)
    if version == _contracts_module.MANIFEST_VERSION and manifest.get(
        "file_count"
    ) != len(entries):
        raise RuntimeError("backup_manifest_invalid")
    if version == _contracts_module.MANIFEST_VERSION:
        expected_provider_id = str(
            getattr(backend, "provider_id", backend.backend_name)
        )
        expected_transport = str(getattr(backend, "transport", backend.backend_name))
        namespaces = sorted({str(item["namespace"]) for item in entries.values()})
        if (
            manifest.get("provider_id") != expected_provider_id
            or manifest.get("transport") != expected_transport
            or manifest.get("namespaces") != namespaces
            or manifest.get("namespace")
            != (namespaces[0] if len(namespaces) == 1 else None)
        ):
            raise RuntimeError("backup_storage_namespace_mismatch")
    return manifest, entries


def _validate_restore_key(key: str) -> None:
    """Delegate destination policy to the active storage backend."""
    try:
        get_backend().validate_restore_key(key)
    except Exception as exc:
        raise RuntimeError("backup_restore_key_outside_storage") from exc
