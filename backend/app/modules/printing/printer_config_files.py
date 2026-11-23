"""Bounded Moonraker config-root transfers with optimistic revision checks."""

from __future__ import annotations

import asyncio
import json
import unicodedata
from enum import StrEnum
from hashlib import sha256
from typing import Any
from urllib.parse import quote
from weakref import WeakValueDictionary

import httpx
from printstash_core.printers import MoonrakerConfig

from app.modules.printing import moonraker
from app.schemas.printer_config_files import (
    MAX_CONFIG_BYTES,
    PrinterConfigFile,
    PrinterConfigFileEntry,
)

_MAX_METADATA_BYTES = 1024 * 1024
_TIMEOUT = 15.0
_LOCKS: WeakValueDictionary[tuple[asyncio.AbstractEventLoop, str], asyncio.Lock]
_LOCKS = WeakValueDictionary()
_ROOT = "config"


class ConfigFileProblem(StrEnum):
    INVALID_PATH = "printer_config_invalid_path"
    NOT_FOUND = "printer_config_file_not_found"
    READ_ONLY = "printer_config_read_only"
    TOO_LARGE = "printer_config_too_large"
    NOT_TEXT = "printer_config_not_text"
    CONFLICT = "printer_config_conflict"
    UNAVAILABLE = "printer_config_unavailable"
    OUTCOME_UNKNOWN = "printer_config_save_unconfirmed"


class ConfigFileError(RuntimeError):
    def __init__(self, problem: ConfigFileProblem) -> None:
        super().__init__(problem)
        self.problem = problem


def _valid_path(path: str) -> bool:
    if not path or len(path) > 512 or not path.lower().endswith((".cfg", ".conf")):
        return False
    if any(not char.isprintable() or char in '\\%?#:"' for char in path):
        return False
    return all(
        part and not part.startswith(".") and part == part.strip()
        for part in path.split("/")
    )


def _text_bytes(content: str) -> bytes:
    if any(
        char not in "\t\r\n" and unicodedata.category(char) == "Cc" for char in content
    ):
        raise ConfigFileError(ConfigFileProblem.NOT_TEXT)
    try:
        raw = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ConfigFileError(ConfigFileProblem.NOT_TEXT) from exc
    if len(raw) > MAX_CONFIG_BYTES:
        raise ConfigFileError(ConfigFileProblem.TOO_LARGE)
    return raw


def _metadata(payload: bytes, expected: type[list] | type[dict]) -> Any:
    try:
        document = json.loads(payload)
    except (ValueError, RecursionError) as exc:
        raise ConfigFileError(ConfigFileProblem.UNAVAILABLE) from exc
    if not isinstance(document, dict) or not isinstance(
        document.get("result"), expected
    ):
        raise ConfigFileError(ConfigFileProblem.UNAVAILABLE)
    return document["result"]


class PrinterConfigFilesClient:
    """Only accesses fixed file-manager routes on a registered Moonraker host."""

    def __init__(self, config: MoonrakerConfig) -> None:
        try:
            base = httpx.URL(config.base_url)
        except httpx.InvalidURL as exc:
            raise ConfigFileError(ConfigFileProblem.UNAVAILABLE) from exc
        if (
            base.scheme not in ("http", "https")
            or not base.host
            or base.query
            or base.fragment
            or base.userinfo
        ):
            raise ConfigFileError(ConfigFileProblem.UNAVAILABLE)
        self._base = str(base).rstrip("/")
        self._headers = {"Accept-Encoding": "identity", "Cache-Control": "no-cache"}
        if config.api_key:
            self._headers["X-Api-Key"] = config.api_key

    async def _get(
        self,
        endpoint: str,
        *,
        params: dict[str, str] | None = None,
        limit: int = _MAX_METADATA_BYTES,
    ) -> bytes:
        try:
            async with (
                asyncio.timeout(_TIMEOUT),
                moonraker.get_http_client().stream(
                    "GET",
                    self._base + endpoint,
                    params=params,
                    headers=self._headers,
                    follow_redirects=False,
                ) as response,
            ):
                if response.status_code == 404:
                    raise ConfigFileError(ConfigFileProblem.NOT_FOUND)
                response.raise_for_status()
                if response.headers.get("content-encoding") not in (None, "identity"):
                    raise ConfigFileError(ConfigFileProblem.UNAVAILABLE)
                content = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=16384):
                    if len(content) + len(chunk) > limit:
                        raise ConfigFileError(ConfigFileProblem.TOO_LARGE)
                    content.extend(chunk)
                return bytes(content)
        except (httpx.HTTPError, TimeoutError) as exc:
            raise ConfigFileError(ConfigFileProblem.UNAVAILABLE) from exc

    async def list_files(self) -> list[PrinterConfigFileEntry]:
        roots = _metadata(await self._get("/server/files/roots"), list)
        root = next(
            (
                row
                for row in roots
                if isinstance(row, dict) and row.get("name") == _ROOT
            ),
            None,
        )
        if root is None or root.get("permissions") not in ("r", "rw"):
            raise ConfigFileError(ConfigFileProblem.UNAVAILABLE)
        listing = _metadata(
            await self._get("/server/files/list", params={"root": _ROOT}), list
        )
        files: dict[str, PrinterConfigFileEntry] = {}
        for row in listing:
            if not isinstance(row, dict) or row.get("permissions") not in ("r", "rw"):
                continue
            path, size, modified = row.get("path"), row.get("size"), row.get("modified")
            if (
                not isinstance(path, str)
                or not _valid_path(path)
                or type(size) is not int
                or not 0 <= size <= MAX_CONFIG_BYTES
                or not isinstance(modified, (int, float))
                or isinstance(modified, bool)
                or not 0 <= modified <= 1e15
            ):
                continue
            if path in files:
                raise ConfigFileError(ConfigFileProblem.UNAVAILABLE)
            files[path] = PrinterConfigFileEntry(
                path=path,
                size_bytes=size,
                modified=modified,
                writable=root["permissions"] == row["permissions"] == "rw",
            )
        return sorted(
            files.values(), key=lambda file: (file.path.casefold(), file.path)
        )

    async def read(self, path: str) -> PrinterConfigFile:
        if not _valid_path(path):
            raise ConfigFileError(ConfigFileProblem.INVALID_PATH)
        entry = next(
            (entry for entry in await self.list_files() if entry.path == path), None
        )
        if entry is None:
            raise ConfigFileError(ConfigFileProblem.NOT_FOUND)
        parent, _, filename = path.rpartition("/")
        directory = _metadata(
            await self._get(
                "/server/files/directory",
                params={"path": f"{_ROOT}/{parent}" if parent else _ROOT},
            ),
            dict,
        )
        files = directory.get("files")
        if not isinstance(files, list):
            raise ConfigFileError(ConfigFileProblem.UNAVAILABLE)
        matches = [
            row
            for row in files
            if isinstance(row, dict) and row.get("filename") == filename
        ]
        if len(matches) != 1 or matches[0].get("permissions") not in ("r", "rw"):
            raise ConfigFileError(ConfigFileProblem.NOT_FOUND)
        raw = await self._get(
            f"/server/files/{_ROOT}/"
            + "/".join(quote(part, safe="") for part in path.split("/")),
            limit=MAX_CONFIG_BYTES,
        )
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ConfigFileError(ConfigFileProblem.NOT_TEXT) from exc
        _text_bytes(content)
        return PrinterConfigFile(
            path=path,
            content=content,
            revision=sha256(raw).hexdigest(),
            writable=entry.writable and matches[0]["permissions"] == "rw",
        )

    async def save(
        self, path: str, content: str, base_revision: str
    ) -> PrinterConfigFile:
        raw = _text_bytes(content)
        key = (asyncio.get_running_loop(), self._base)
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = asyncio.Lock()
        async with lock:
            current = await self.read(path)
            if not current.writable:
                raise ConfigFileError(ConfigFileProblem.READ_ONLY)
            if current.revision != base_revision:
                raise ConfigFileError(ConfigFileProblem.CONFLICT)
            if current.content == content:
                return current
            await self._upload(path, raw)
            try:
                saved = await self.read(path)
            except ConfigFileError as exc:
                raise ConfigFileError(ConfigFileProblem.OUTCOME_UNKNOWN) from exc
            if saved.content != content:
                raise ConfigFileError(ConfigFileProblem.OUTCOME_UNKNOWN)
            return saved

    async def _upload(self, path: str, raw: bytes) -> None:
        parent, _, filename = path.rpartition("/")
        fields = {"root": _ROOT, "checksum": sha256(raw).hexdigest()}
        if parent:
            fields["path"] = parent
        try:
            async with (
                asyncio.timeout(_TIMEOUT),
                moonraker.get_http_client().stream(
                    "POST",
                    self._base + "/server/files/upload",
                    data=fields,
                    files={"file": (filename, raw, "application/octet-stream")},
                    headers=self._headers,
                    follow_redirects=False,
                ) as response,
            ):
                if response.status_code in (401, 403, 405):
                    raise ConfigFileError(ConfigFileProblem.READ_ONLY)
                response.raise_for_status()
        except (httpx.HTTPError, TimeoutError) as exc:
            raise ConfigFileError(ConfigFileProblem.OUTCOME_UNKNOWN) from exc
