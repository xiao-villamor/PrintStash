"""Incremental WebDAV depth-one responses with bounded XML retention."""

from __future__ import annotations

from contextlib import contextmanager
from email.utils import parsedate_to_datetime
from typing import Iterator
from urllib.parse import unquote, urlsplit

import httpx
import lxml.etree as etree

from app.core.time import ensure_utc
from app.services.remote_deadline import operation_timeout
from app.services.remote_io import RemoteEntry
from app.services.storage_backend import StorageConfigurationError

_DAV = "{DAV:}"
_PROPERTIES = b"""<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:getcontentlength/><d:getlastmodified/><d:getetag/></d:prop></d:propfind>"""


def _entry(response, root_path: str) -> RemoteEntry | None:
    href = response.findtext(f"{_DAV}href")
    if href is None:
        raise StorageConfigurationError("webdav_listing_href_missing")
    path = unquote(urlsplit(href).path, errors="strict").rstrip("/")
    root_path = root_path.rstrip("/")
    if path == root_path:
        return None
    if not path.startswith(root_path + "/"):
        raise StorageConfigurationError("webdav_listing_outside_root")
    key = path[len(root_path) + 1 :]
    if any(part in {"", ".", ".."} for part in key.split("/")):
        raise StorageConfigurationError("webdav_listing_key_invalid")
    properties = {}
    for propstat in response.findall(f"{_DAV}propstat"):
        status = (propstat.findtext(f"{_DAV}status") or "").split()
        if len(status) < 2 or not status[1].isdigit():
            raise StorageConfigurationError("webdav_listing_status_invalid")
        if not 200 <= int(status[1]) < 300:
            continue
        prop = propstat.find(f"{_DAV}prop")
        if prop is not None:
            properties.update((child.tag, child) for child in prop)
    kind = properties.get(f"{_DAV}resourcetype")
    is_dir = kind is not None and kind.find(f"{_DAV}collection") is not None
    length = properties.get(f"{_DAV}getcontentlength")
    if is_dir:
        size = 0
    elif length is None or length.text is None:
        raise StorageConfigurationError("webdav_listing_size_missing")
    else:
        size = int(length.text)
    if size < 0:
        raise StorageConfigurationError("webdav_listing_size_invalid")
    modified = properties.get(f"{_DAV}getlastmodified")
    modified_at = None
    if modified is not None and modified.text:
        try:
            modified_at = ensure_utc(parsedate_to_datetime(modified.text))
        except (TypeError, ValueError, OverflowError):
            pass
    etag = properties.get(f"{_DAV}getetag")
    return RemoteEntry(
        key, size, is_dir, modified_at, etag.text if etag is not None else None
    )


@contextmanager
def iter_webdav_directory(
    url: str, *, root_url: str, username: str, password: str
) -> Iterator[Iterator[RemoteEntry]]:
    """Own the HTTP body until the caller closes or exhausts the iterator."""
    timeout = operation_timeout()
    with httpx.stream(
        "PROPFIND",
        url,
        headers={"Depth": "1", "Content-Type": "application/xml"},
        content=_PROPERTIES,
        auth=(username, password),
        timeout=timeout,
        follow_redirects=False,
    ) as response:
        if response.status_code != 207:
            raise StorageConfigurationError(
                f"webdav_listing_failed:{response.status_code}"
            )
        root_path = unquote(urlsplit(root_url).path, errors="strict")

        def entries():
            parser = etree.XMLPullParser(
                events=("end",),
                tag=f"{_DAV}response",
                resolve_entities=False,
                no_network=True,
                load_dtd=False,
            )
            for chunk in response.iter_bytes(chunk_size=64 * 1024):
                operation_timeout()
                parser.feed(chunk)
                for _, element in parser.read_events():
                    entry = _entry(element, root_path)
                    parent = element.getparent()
                    if parent is not None:
                        parent.remove(element)
                    element.clear()
                    if entry is not None:
                        yield entry
            parser.close()

        iterator = entries()
        try:
            yield iterator
        finally:
            iterator.close()
