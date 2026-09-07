"""Object-scoped browser delivery capabilities, independent of HTTP frameworks."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote


@dataclass(frozen=True)
class BrowserDownload:
    url: str = field(repr=False)
    expires_at: datetime
    key: str = field(repr=False)
    method: str = "GET"
    required_headers: tuple[str, ...] = ()
    cors_origin: str | None = None
    version_id: str | None = None


def content_disposition(filename: str, *, inline: bool = False) -> str:
    """Encode an untrusted display name without path or header injection."""
    leaf = filename.replace("\\", "/").rsplit("/", 1)[-1]
    leaf = "".join(char for char in leaf if ord(char) >= 32 and ord(char) != 127)
    if not leaf.strip() or leaf in {".", ".."}:
        leaf = "download"
    leaf = leaf[:512]
    fallback = leaf.encode("ascii", "replace").decode().replace('"', "_")
    kind = "inline" if inline else "attachment"
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(leaf, safe='')}"
