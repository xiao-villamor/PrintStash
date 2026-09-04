"""Bounded, transport-free contracts for captured external models.

The objects in this module intentionally separate the transient download
descriptor from the persisted capture manifest.  In particular, a signed URL
is useful only while downloading and must never be serialised into a manifest.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Mapping
from urllib.parse import unquote, urlsplit, urlunsplit

MAX_MANIFEST_BYTES = 256 * 1024
MAX_URL_LENGTH = 2 * 1024
MAX_ID_LENGTH = 256
MAX_FILENAME_LENGTH = 512
MAX_FIELD_VALUE_LENGTHS: dict[str, int] = {
    "title": 512,
    "description": 64 * 1024,
    "instructions": 128 * 1024,
    "creator_name": 512,
    "creator_id": 255,
    "creator_url": 2048,
    "license_code": 255,
    "license_url": 2048,
    "license_text": 64 * 1024,
    "attribution_text": 64 * 1024,
    "published_at": 64,
    "updated_at": 64,
}
MAX_TAGS = 100
_HTML_TAG = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")
_SAFE_PATH_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]*\Z")
_MAKERWORLD_LOCALE = re.compile(r"[A-Za-z]{2}(?:-[A-Za-z]{2,})?\Z")
_CULTS_LOCALE = re.compile(r"[A-Za-z]{2}\Z")

# These are page hosts, deliberately separate from download/API hosts.  A
# capture's canonical URL must identify the provider page the user saw; a CDN
# or an API endpoint is not a substitute for that identity.
_PROVIDER_PAGE_HOSTS: dict[str, frozenset[str]] = {
    "printables": frozenset({"printables.com", "www.printables.com"}),
    # MakerWorld serves some pages from first-party subdomains (for example,
    # assets.makerworld.com).  A suffix check below still rejects
    # makerworld.com.attacker.test.
    "makerworld": frozenset({"makerworld.com"}),
    "thingiverse": frozenset({"thingiverse.com", "www.thingiverse.com"}),
    "cults": frozenset({"cults3d.com", "www.cults3d.com"}),
    "myminifactory": frozenset(
        {
            "myminifactory.com",
            "www.myminifactory.com",
        }
    ),
}


class CaptureContractError(ValueError):
    """Raised when untrusted capture data cannot enter the bounded contract."""


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise CaptureContractError(f"{name} must be an object")
    return value


def _strict_keys(value: Mapping[str, Any], expected: set[str], name: str) -> None:
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown:
        raise CaptureContractError(
            f"{name} contains unknown fields: {sorted(unknown)!r}"
        )
    if missing:
        raise CaptureContractError(f"{name} is missing fields: {sorted(missing)!r}")


def _bounded_string(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value:
        raise CaptureContractError(f"{name} must be a non-empty string")
    value = (
        unicodedata.normalize("NFC", value).replace("\r\n", "\n").replace("\r", "\n")
    )
    if len(value) > maximum:
        raise CaptureContractError(f"{name} exceeds {maximum} characters")
    if any(
        unicodedata.category(character).startswith("C") and character != "\n"
        for character in value
    ):
        raise CaptureContractError(f"{name} must not contain control data")
    if _HTML_TAG.search(value):
        raise CaptureContractError(f"{name} must not contain HTML or control data")
    return value


def _iso8601(value: Any, name: str, maximum: int) -> str:
    text = _bounded_string(value, name, maximum)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise CaptureContractError(f"{name} must be an ISO-8601 datetime") from error
    return text


def _tags(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > MAX_TAGS:
        raise CaptureContractError(
            f"source.tags must contain at most {MAX_TAGS} strings"
        )
    tags = tuple(_bounded_string(tag, "source.tags[]", 255) for tag in value)
    if len(set(tags)) != len(tags):
        raise CaptureContractError("source.tags must not contain duplicates")
    return tags


def sanitize_canonical_url(value: Any) -> str:
    """Return an origin URL without query/fragment data, or reject it."""
    url = _bounded_string(value, "canonical_url", MAX_URL_LENGTH)
    try:
        split = urlsplit(url)
        # Accessing ``port`` forces urlsplit to reject malformed authorities
        # such as a non-numeric port.  Provider URL validation rejects ports,
        # while this generic helper retains them for non-provider sources.
        port = split.port
    except ValueError as error:
        raise CaptureContractError(
            "canonical_url must be an absolute HTTP(S) URL"
        ) from error
    if (
        split.scheme not in {"http", "https"}
        or not split.hostname
        or split.username
        or split.password
    ):
        raise CaptureContractError("canonical_url must be an absolute HTTP(S) URL")
    try:
        hostname = split.hostname.encode("idna").decode("ascii").lower()
    except UnicodeError as error:
        raise CaptureContractError("canonical_url host is invalid") from error
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname if port is None else f"{hostname}:{port}"
    return urlunsplit((split.scheme.lower(), netloc, split.path, "", ""))


def _path_segments(path: str) -> tuple[list[str], bool]:
    """Return non-empty path segments and whether the path had a final slash.

    URL paths are validated before provider matching.  In particular, encoded
    separators and dot segments are rejected instead of being interpreted
    differently by a browser, proxy, or provider server.
    """
    if "\\" in path:
        raise CaptureContractError("canonical_url path is invalid")
    try:
        decoded = unquote(path)
    except (TypeError, ValueError) as error:
        raise CaptureContractError("canonical_url path is invalid") from error
    if any(marker in decoded for marker in ("\\", "\x00")):
        raise CaptureContractError("canonical_url path is invalid")
    raw_segments = path.split("/")
    trailing_slash = bool(raw_segments and raw_segments[-1] == "")
    if any(segment == "" for segment in raw_segments[1:-1]):
        raise CaptureContractError("canonical_url path is invalid")
    segments = raw_segments[1:-1] if trailing_slash else raw_segments[1:]
    if not segments or any(segment in {".", ".."} for segment in segments):
        raise CaptureContractError("canonical_url path is invalid")
    # Reject encoded slash/dot traversal and any other encoded character that
    # would make the durable path differ from the path being validated.  Real
    # provider model routes use ASCII IDs/slugs, so accepting these adds no
    # useful compatibility.
    if decoded != path:
        raise CaptureContractError("canonical_url path is invalid")
    if not all(
        _SAFE_PATH_SEGMENT.fullmatch(segment)
        or re.fullmatch(r"thing:\d+", segment, re.IGNORECASE)
        for segment in segments
    ):
        raise CaptureContractError("canonical_url path is invalid")
    return segments, trailing_slash


def _item_segment_matches(segment: str, source_item_id: str | None) -> bool:
    """Match an ID-bearing slug without confusing a numeric prefix.

    Printables and MakerWorld commonly render ``<id>-<slug>`` while their
    capture IDs contain only ``<id>``.  Test fixtures and older portable
    captures can carry a full slug as the ID, so an exact match remains valid.
    """
    if source_item_id is None:
        return True
    return segment == source_item_id or segment.startswith(f"{source_item_id}-")


def _provider_host_matches(provider: str, hostname: str) -> bool:
    allowed = _PROVIDER_PAGE_HOSTS[provider]
    if hostname in allowed:
        return True
    return provider == "makerworld" and hostname.endswith(".makerworld.com")


def canonicalize_provider_url(
    provider: Any, value: Any, source_item_id: str | None = None
) -> str:
    """Validate and normalize a provider page URL.

    The generic :func:`sanitize_canonical_url` is intentionally permissive for
    portable captures from providers that PrintStash does not know yet.  For
    the providers with built-in capture adapters this boundary additionally
    binds the hostname and page route.  Query strings/fragments are discarded
    because they may carry tracking or signed credentials; userinfo and
    malformed paths are rejected rather than normalized into a different
    identity.

    Cults is the one provider whose API item ID is intentionally opaque and
    differs from the page slug.  Its host/route are still bound, but the page
    slug comparison is owned by the Cults provider adapter.
    """
    provider_name = _bounded_string(provider, "source.provider", 64)
    normalized = sanitize_canonical_url(value)
    if provider_name not in _PROVIDER_PAGE_HOSTS:
        return normalized

    try:
        split = urlsplit(normalized)
        port = split.port
    except ValueError as error:
        raise CaptureContractError(
            "canonical_url provider binding is invalid"
        ) from error
    hostname = split.hostname
    if (
        not hostname
        or port is not None
        or not _provider_host_matches(provider_name, hostname.lower())
    ):
        raise CaptureContractError("canonical_url host does not match provider")
    segments, trailing_slash = _path_segments(split.path)
    normalized_segments = list(segments)

    if provider_name == "printables":
        # ``/files`` is a real model-page view used by the browser extension.
        if len(segments) not in {2, 3} or segments[0].lower() != "model":
            raise CaptureContractError("canonical_url path does not match provider")
        if len(segments) == 3 and segments[2].lower() != "files":
            raise CaptureContractError("canonical_url path does not match provider")
        if not _item_segment_matches(segments[1], source_item_id):
            raise CaptureContractError("canonical_url item does not match source")
        normalized_segments[0] = "model"
        if len(normalized_segments) == 3:
            normalized_segments[2] = "files"
    elif provider_name == "makerworld":
        if len(segments) == 2:
            locale = None
            model_index = 0
        elif len(segments) == 3:
            locale = segments[0]
            model_index = 1
            if not _MAKERWORLD_LOCALE.fullmatch(locale):
                raise CaptureContractError("canonical_url locale is invalid")
        else:
            raise CaptureContractError("canonical_url path does not match provider")
        if segments[model_index].lower() != "models":
            raise CaptureContractError("canonical_url path does not match provider")
        item_segment = segments[model_index + 1]
        if not _item_segment_matches(item_segment, source_item_id):
            raise CaptureContractError("canonical_url item does not match source")
        if locale is not None:
            normalized_segments[0] = locale.lower()
            normalized_segments[1] = "models"
        else:
            normalized_segments[0] = "models"
    elif provider_name == "thingiverse":
        if len(segments) == 1 and re.fullmatch(
            r"thing:\d+", segments[0], re.IGNORECASE
        ):
            _, item_segment = segments[0].split(":", 1)
            normalized_segments[0] = f"thing:{item_segment}"
        elif (
            len(segments) in {2, 3}
            and segments[0].lower() == "things"
            and segments[1].isdigit()
            and (len(segments) == 2 or segments[2].lower() == "files")
        ):
            item_segment = segments[1]
            normalized_segments[0] = "things"
            if len(normalized_segments) == 3:
                normalized_segments[2] = "files"
        elif (
            len(segments) == 2
            and re.fullmatch(r"thing:\d+", segments[0], re.IGNORECASE)
            and segments[1].lower() == "files"
        ):
            _, item_segment = segments[0].split(":", 1)
            normalized_segments[0] = f"thing:{item_segment}"
            normalized_segments[1] = "files"
        else:
            raise CaptureContractError("canonical_url path does not match provider")
        if not _item_segment_matches(item_segment, source_item_id):
            raise CaptureContractError("canonical_url item does not match source")
    elif provider_name == "cults":
        if len(segments) not in {3, 4}:
            raise CaptureContractError("canonical_url path does not match provider")
        route_index = 0
        if len(segments) == 4:
            if not _CULTS_LOCALE.fullmatch(segments[0]):
                raise CaptureContractError("canonical_url locale is invalid")
            normalized_segments[0] = segments[0].lower()
            route_index = 1
        if (
            segments[route_index].lower() != "3d-model"
            or not segments[route_index + 1]
            or not segments[route_index + 2]
        ):
            raise CaptureContractError("canonical_url path does not match provider")
        normalized_segments[route_index] = "3d-model"
    else:  # myminifactory
        if len(segments) != 2 or segments[0].lower() != "object":
            raise CaptureContractError("canonical_url path does not match provider")
        if not _item_segment_matches(segments[1], source_item_id):
            raise CaptureContractError("canonical_url item does not match source")
        normalized_segments[0] = "object"

    # Route/locale tokens are case-insensitive, while provider slugs remain
    # untouched.  Dropping a final slash makes equivalent page URLs stable.
    normalized_path = "/" + "/".join(normalized_segments)
    if trailing_slash:
        # The URL was structurally valid, but the canonical representation is
        # stable without a route-insignificant trailing slash.
        trailing_slash = False
    del trailing_slash
    return urlunsplit(
        (split.scheme.lower(), split.netloc.lower(), normalized_path, "", "")
    )


# Descriptive compatibility name for callers that use the existing
# ``sanitize_*`` vocabulary.  Keep one implementation so all consumers share
# exactly the same host/path binding rules.
sanitize_provider_canonical_url = canonicalize_provider_url


@dataclass(frozen=True, slots=True)
class CapturedField:
    value: str
    origin: Literal["confirmed", "inferred"]

    @classmethod
    def from_dict(cls, data: Any, field_name: str) -> CapturedField:
        value = _require_mapping(data, f"fields.{field_name}")
        _strict_keys(value, {"value", "origin"}, f"fields.{field_name}")
        if value["origin"] not in {"confirmed", "inferred"}:
            raise CaptureContractError(f"fields.{field_name}.origin is invalid")
        field_value = (
            _iso8601(
                value["value"],
                f"fields.{field_name}.value",
                MAX_FIELD_VALUE_LENGTHS[field_name],
            )
            if field_name in {"published_at", "updated_at"}
            else _bounded_string(
                value["value"],
                f"fields.{field_name}.value",
                MAX_FIELD_VALUE_LENGTHS[field_name],
            )
        )
        if field_name in {"creator_url", "license_url"}:
            field_value = sanitize_canonical_url(field_value)
        return cls(
            value=field_value,
            origin=value["origin"],
        )

    def to_dict(self) -> dict[str, str]:
        return {"value": self.value, "origin": self.origin}


@dataclass(frozen=True, slots=True)
class CaptureSource:
    provider: str
    canonical_url: str
    source_item_id: str | None
    source_revision: str | None
    adapter_version: str
    tags: tuple[str, ...]
    fields: dict[str, CapturedField]

    @classmethod
    def from_dict(cls, data: Any) -> CaptureSource:
        value = _require_mapping(data, "source")
        required = {
            "provider",
            "canonical_url",
            "source_item_id",
            "source_revision",
            "adapter_version",
            "fields",
        }
        unknown = set(value) - (required | {"tags"})
        missing = required - set(value)
        if unknown:
            raise CaptureContractError(
                f"source contains unknown fields: {sorted(unknown)!r}"
            )
        if missing:
            raise CaptureContractError(f"source is missing fields: {sorted(missing)!r}")
        provider = _bounded_string(value["provider"], "source.provider", 64)
        source_item_id = value["source_item_id"]
        if source_item_id is not None:
            source_item_id = _bounded_string(
                source_item_id, "source.source_item_id", 255
            )
        adapter_version = _bounded_string(
            value["adapter_version"], "source.adapter_version", 64
        )
        revision = value["source_revision"]
        if revision is not None:
            revision = _bounded_string(
                revision, "source.source_revision", MAX_ID_LENGTH
            )
        raw_fields = _require_mapping(value["fields"], "source.fields")
        unknown_fields = set(raw_fields) - set(MAX_FIELD_VALUE_LENGTHS)
        if unknown_fields:
            raise CaptureContractError(
                f"source.fields contains unknown fields: {sorted(unknown_fields)!r}"
            )
        # Sparse provider captures omit fields entirely.  CapturedField keeps
        # its non-empty value contract so a transport placeholder (for
        # example, an empty database sentinel) can never become fake capture
        # history when a manifest is parsed.
        fields = {
            name: CapturedField.from_dict(raw, name) for name, raw in raw_fields.items()
        }
        return cls(
            provider=provider,
            canonical_url=canonicalize_provider_url(
                provider, value["canonical_url"], source_item_id
            ),
            source_item_id=source_item_id,
            source_revision=revision,
            adapter_version=adapter_version,
            tags=_tags(value.get("tags", [])),
            fields=fields,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "canonical_url": self.canonical_url,
            "source_item_id": self.source_item_id,
            "source_revision": self.source_revision,
            "adapter_version": self.adapter_version,
            "tags": list(self.tags),
            "fields": {name: field.to_dict() for name, field in self.fields.items()},
        }


@dataclass(frozen=True, slots=True)
class CaptureFile:
    id: str
    name: str
    file_type: str
    size: int | None

    @classmethod
    def from_dict(cls, data: Any) -> CaptureFile:
        value = _require_mapping(data, "file")
        _strict_keys(value, {"id", "name", "file_type", "size"}, "file")
        size = value["size"]
        if size is not None and (
            not isinstance(size, int) or isinstance(size, bool) or size < 0
        ):
            raise CaptureContractError(
                "file.size must be a non-negative integer or null"
            )
        return cls(
            id=_bounded_string(value["id"], "file.id", MAX_ID_LENGTH),
            name=_bounded_string(value["name"], "file.name", MAX_FILENAME_LENGTH),
            file_type=_bounded_string(value["file_type"], "file.file_type", 32),
            size=size,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "file_type": self.file_type,
            "size": self.size,
        }


@dataclass(frozen=True, slots=True)
class CaptureManifestV2:
    source: CaptureSource
    files: tuple[CaptureFile, ...]
    selected_ids: tuple[str, ...]
    kind: Literal["model_files"] = "model_files"
    schema_version: Literal[2] = 2

    @classmethod
    def from_dict(cls, data: Any) -> CaptureManifestV2:
        value = _require_mapping(data, "manifest")
        _strict_keys(
            value,
            {"schema_version", "kind", "source", "files", "selected_ids"},
            "manifest",
        )
        if value["schema_version"] != 2 or value["kind"] != "model_files":
            raise CaptureContractError(
                "manifest must be a model_files schema version 2 capture"
            )
        if not isinstance(value["files"], list) or not value["files"]:
            raise CaptureContractError("manifest.files must be a non-empty list")
        files = tuple(CaptureFile.from_dict(file) for file in value["files"])
        if len({file.id for file in files}) != len(files):
            raise CaptureContractError("manifest.files contains duplicate ids")
        if not isinstance(value["selected_ids"], list) or not value["selected_ids"]:
            raise CaptureContractError("manifest.selected_ids must be a non-empty list")
        selected_ids = tuple(
            _bounded_string(item, "selected_id", MAX_ID_LENGTH)
            for item in value["selected_ids"]
        )
        if len(set(selected_ids)) != len(selected_ids) or not set(selected_ids) <= {
            file.id for file in files
        }:
            raise CaptureContractError(
                "manifest.selected_ids must refer to unique files"
            )
        manifest = cls(
            source=CaptureSource.from_dict(value["source"]),
            files=files,
            selected_ids=selected_ids,
        )
        if (
            len(json.dumps(manifest.to_dict(), separators=(",", ":")).encode())
            > MAX_MANIFEST_BYTES
        ):
            raise CaptureContractError("manifest exceeds maximum size")
        return manifest

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "source": self.source.to_dict(),
            "files": [file.to_dict() for file in self.files],
            "selected_ids": list(self.selected_ids),
        }


@dataclass(frozen=True, slots=True)
class ResolvedAsset:
    """A selected external file with an in-memory-only download descriptor."""

    manifest: CaptureManifestV2
    source_selection_id: str
    source_file_id: str | None
    source_filename: str
    download_url: str
    source_item_id: str
    member_source_id: str | None = None
    member_url: str | None = None


@dataclass(frozen=True, slots=True)
class StagedAsset:
    """A resolved asset after a local stream/hash operation."""

    resolved: ResolvedAsset
    staged_path: Path
    result_key: str
    blob_sha256: str
    container_entry_path: str | None = None

    @property
    def source_selection_id(self) -> str:
        return self.resolved.source_selection_id

    @property
    def manifest(self) -> CaptureManifestV2:
        return self.resolved.manifest


__all__ = [
    "CaptureContractError",
    "CaptureFile",
    "CaptureManifestV2",
    "CaptureSource",
    "CapturedField",
    "MAX_MANIFEST_BYTES",
    "ResolvedAsset",
    "StagedAsset",
    "canonicalize_provider_url",
    "sanitize_canonical_url",
    "sanitize_provider_canonical_url",
]
