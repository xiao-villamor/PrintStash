"""Pure URL and provider-payload rules used by import resolvers.

This module deliberately contains no transport, browser, logging, persistence,
or application error policy. Host applications retain those effects and call
these deterministic rules to classify pages and normalize provider payloads.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import urlsplit

from .contracts import CaptureContractError, CaptureManifestV2

MODEL_EXTENSIONS = (
    ".zip",
    ".3mf",
    ".stl",
    ".obj",
    ".step",
    ".stp",
    ".gcode",
    ".g",
    ".gco",
    ".bgcode",
)
PRINTABLES_HOSTS = {"printables.com", "www.printables.com"}
THINGIVERSE_HOSTS = {"thingiverse.com", "www.thingiverse.com"}
CHALLENGE_MARKERS = (
    "just a moment",
    "challenge-platform",
    "cf-chl",
    "verifying you are human",
    "/cdn-cgi/challenge-platform/",
)
PRINTABLES_FILE_CATEGORIES = (
    ("stls", "stl"),
    ("gcodes", "gcode"),
    ("slas", "sla"),
    ("otherFiles", "other"),
)
_MEMBER_LIST_HINTS = (
    "design",
    "model",
    "content",
    "hit",
    "item",
    "list",
    "record",
    "favorite",
)


@dataclass
class ModelFile:
    """One selectable downloadable file on a model page."""

    file_id: str
    name: str
    file_type: str
    size: int | None = None


@dataclass
class CollectionMember:
    """One model belonging to a collection."""

    page_url: str
    title: str
    source_id: str


def host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def printables_id(url: str) -> str | None:
    match = re.search(r"/model/(\d+)", urlsplit(url).path)
    return match.group(1) if match else None


def makerworld_id(url: str) -> str | None:
    match = re.search(r"/models/(\d+)", urlsplit(url).path)
    return match.group(1) if match else None


def thingiverse_id(url: str) -> str | None:
    path = urlsplit(url).path
    match = re.search(r"thing:(\d+)", path) or re.search(r"/things/(\d+)", path)
    return match.group(1) if match else None


def collection_id(url: str) -> str | None:
    match = re.search(r"/collections/(\d+)", urlsplit(url).path)
    return match.group(1) if match else None


def classify_collection(url: str) -> str | None:
    """Return the provider name for a known collection URL, else ``None``."""
    hostname = host(url)
    if hostname in PRINTABLES_HOSTS and collection_id(url):
        return "printables"
    if (
        hostname == "makerworld.com" or hostname.endswith(".makerworld.com")
    ) and collection_id(url):
        return "makerworld"
    return None


def classify_page(url: str) -> str | None:
    """Return the provider name for a known model page, else ``None``."""
    hostname = host(url)
    if hostname in PRINTABLES_HOSTS and printables_id(url):
        return "printables"
    if (
        hostname == "makerworld.com" or hostname.endswith(".makerworld.com")
    ) and makerworld_id(url):
        return "makerworld"
    if hostname in THINGIVERSE_HOSTS and thingiverse_id(url):
        return "thingiverse"
    return None


def looks_like_download(url: str) -> bool:
    lower = url.split("?", 1)[0].lower()
    return lower.endswith(MODEL_EXTENSIONS) or "/download" in url.lower()


def first_download_url(data: Any) -> str | None:
    """Walk JSON breadth-first for the first plausible absolute download URL."""
    stack: list[Any] = [data]
    fallback: str | None = None
    while stack:
        current = stack.pop(0)
        if isinstance(current, dict):
            for key in ("url", "downloadUrl", "download_url", "link"):
                value = current.get(key)
                if isinstance(value, str) and value.startswith(("http://", "https://")):
                    return value
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
        elif isinstance(current, str):
            if (
                fallback is None
                and current.startswith(("http://", "https://"))
                and looks_like_download(current)
            ):
                fallback = current
    return fallback


def looks_like_challenge(html: str) -> bool:
    """Return whether HTML is a Cloudflare challenge rather than page content."""
    if "__NEXT_DATA__" in html:
        return False
    lowered = html.lower()
    return any(marker in lowered for marker in CHALLENGE_MARKERS)


def extract_next_data(html: str) -> Any | None:
    """Extract a Next.js ``__NEXT_DATA__`` JSON value from HTML."""
    match = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    try:
        return json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        return None


def pick_printables_pack(packs: Any) -> str | None:
    """Prefer the all-model-files pack, then the first pack carrying an id."""
    if not isinstance(packs, list):
        return None
    for pack in packs:
        if (
            isinstance(pack, dict)
            and pack.get("fileType") == "MODEL_FILES"
            and pack.get("id")
        ):
            return str(pack["id"])
    for pack in packs:
        if isinstance(pack, dict) and pack.get("id"):
            return str(pack["id"])
    return None


def printables_link_from_output(payload: Any) -> str | None:
    result = (payload or {}).get("data", {}).get("getDownloadLink") or {}
    output = result.get("output") or {}
    if isinstance(output.get("link"), str):
        return output["link"]
    for entry in output.get("files") or []:
        if isinstance(entry, dict) and isinstance(entry.get("link"), str):
            return entry["link"]
    return None


def printables_links_from_output(payload: Any) -> list[str]:
    """Return per-file Printables links, or the single output link as fallback."""
    result = (payload or {}).get("data", {}).get("getDownloadLink") or {}
    output = result.get("output") or {}
    links = [
        entry["link"]
        for entry in output.get("files") or []
        if isinstance(entry, dict) and isinstance(entry.get("link"), str)
    ]
    if links:
        return links
    if isinstance(output.get("link"), str):
        return [output["link"]]
    return []


def printables_files_from_print(print_obj: dict[str, Any]) -> list[ModelFile]:
    """Normalize Printables file buckets into selectable model files."""
    files: list[ModelFile] = []
    for field, file_type in PRINTABLES_FILE_CATEGORIES:
        for entry in print_obj.get(field) or []:
            if isinstance(entry, dict) and entry.get("id"):
                size = entry.get("fileSize")
                files.append(
                    ModelFile(
                        file_id=str(entry["id"]),
                        name=str(entry.get("name") or entry["id"]),
                        file_type=file_type,
                        size=size if isinstance(size, int) else None,
                    )
                )
    return files


def parse_printables_capture(payload: Any, canonical_url: str) -> CaptureManifestV2:
    """Parse a Printables model response into the safe persisted capture shape.

    Provider payloads may contain browser state and signed download links.  This
    adapter takes only the small, reviewed allowlist below and feeds it through
    the strict manifest boundary.
    """
    try:
        print_obj = payload["data"]["print"]
    except (KeyError, TypeError):
        raise CaptureContractError("Printables payload has no print object") from None
    if not isinstance(print_obj, dict):
        raise CaptureContractError("Printables payload print object is invalid")
    source_item_id = print_obj.get("id")
    if source_item_id is not None and not str(source_item_id):
        source_item_id = None

    fields: dict[str, dict[str, str]] = {}
    values = (
        ("title", print_obj.get("name") or print_obj.get("title")),
        ("description", print_obj.get("description")),
        ("instructions", print_obj.get("instructions")),
        ("creator_name", _printables_creator_name(print_obj)),
        ("creator_id", _printables_creator_value(print_obj, "id")),
        (
            "creator_url",
            _printables_creator_value(print_obj, "url", "profileUrl", "profile_url"),
        ),
        ("license_code", _printables_license_code(print_obj)),
        ("license_url", _printables_license_value(print_obj, "url", "link")),
        ("license_text", _printables_license_value(print_obj, "text", "description")),
        (
            "attribution_text",
            print_obj.get("attribution") or print_obj.get("attributionText"),
        ),
    )
    for name, value in values:
        if isinstance(value, str) and value:
            fields[name] = {"value": value, "origin": "confirmed"}

    files = [
        {
            "id": file.file_id,
            "name": file.name,
            "file_type": file.file_type,
            "size": file.size,
        }
        for file in printables_files_from_print(print_obj)
    ]
    return CaptureManifestV2.from_dict(
        {
            "schema_version": 2,
            "kind": "model_files",
            "source": {
                "provider": "printables",
                "canonical_url": canonical_url,
                "source_item_id": str(source_item_id)
                if source_item_id is not None
                else None,
                "source_revision": None,
                "adapter_version": "printables-v1",
                "tags": [],
                "fields": fields,
            },
            "files": files,
            "selected_ids": [file["id"] for file in files],
        }
    )


def _printables_creator_name(print_obj: dict[str, Any]) -> str | None:
    user = print_obj.get("user") or print_obj.get("creator")
    if not isinstance(user, dict):
        return None
    for key in ("handle", "name", "username"):
        value = user.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _printables_creator_value(print_obj: dict[str, Any], *keys: str) -> str | None:
    user = print_obj.get("user") or print_obj.get("creator")
    if not isinstance(user, dict):
        return None
    for key in keys:
        value = user.get(key)
        if isinstance(value, (str, int)) and str(value):
            return str(value)
    return None


def _printables_license_code(print_obj: dict[str, Any]) -> str | None:
    license_value = print_obj.get("license")
    if isinstance(license_value, str):
        return license_value
    if isinstance(license_value, dict):
        for key in ("code", "name", "slug"):
            value = license_value.get(key)
            if isinstance(value, str) and value:
                return value
    return None


def _printables_license_value(print_obj: dict[str, Any], *keys: str) -> str | None:
    license_value = print_obj.get("license")
    if not isinstance(license_value, dict):
        return None
    for key in keys:
        value = license_value.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def makerworld_instance_id(design: Any) -> str | None:
    """Select MakerWorld's default instance, then its first listed instance."""
    if not isinstance(design, dict):
        return None
    if design.get("defaultInstanceId"):
        return str(design["defaultInstanceId"])
    for instance in design.get("instances") or []:
        if isinstance(instance, dict) and instance.get("id"):
            return str(instance["id"])
    return None


def makerworld_collection_title(next_data: Any, collection_id_value: str) -> str:
    """Normalize a MakerWorld favorite/collection title with stable fallback."""
    title = f"Collection {collection_id_value}"
    try:
        props = next_data["props"]["pageProps"]
        meta = props.get("favorite") or props.get("collection") or {}
        if isinstance(meta, dict) and (meta.get("title") or meta.get("name")):
            title = str(meta.get("title") or meta.get("name"))
    except (KeyError, TypeError):
        pass
    return title


def makerworld_collection_members(next_data: Any) -> list[CollectionMember]:
    """Extract unique design-like entries from MakerWorld hydration JSON."""
    try:
        props = next_data["props"]["pageProps"]
    except (KeyError, TypeError):
        return []

    members: list[CollectionMember] = []
    seen: set[str] = set()

    def consider(entry: Any) -> None:
        if not isinstance(entry, dict):
            return
        design = cast(
            dict[Any, Any],
            entry.get("design") if isinstance(entry.get("design"), dict) else entry,
        )
        design_id = design.get("id") or design.get("designId") or entry.get("designId")
        title = design.get("title") or design.get("designTitle") or design.get("name")
        if design_id is None or str(design_id) in seen:
            return
        seen.add(str(design_id))
        members.append(
            CollectionMember(
                page_url=f"https://makerworld.com/en/models/{design_id}",
                title=str(title or design_id),
                source_id=str(design_id),
            )
        )

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, list) and any(
                    hint in key.lower() for hint in _MEMBER_LIST_HINTS
                ):
                    for entry in value:
                        consider(entry)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(props)
    return members


__all__ = [
    "CHALLENGE_MARKERS",
    "CollectionMember",
    "MODEL_EXTENSIONS",
    "ModelFile",
    "PRINTABLES_FILE_CATEGORIES",
    "PRINTABLES_HOSTS",
    "THINGIVERSE_HOSTS",
    "classify_collection",
    "classify_page",
    "collection_id",
    "extract_next_data",
    "first_download_url",
    "host",
    "looks_like_challenge",
    "looks_like_download",
    "makerworld_collection_members",
    "makerworld_collection_title",
    "makerworld_id",
    "makerworld_instance_id",
    "pick_printables_pack",
    "printables_files_from_print",
    "parse_printables_capture",
    "printables_id",
    "printables_link_from_output",
    "printables_links_from_output",
    "thingiverse_id",
]
