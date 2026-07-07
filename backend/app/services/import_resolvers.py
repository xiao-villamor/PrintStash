"""Resolve model *page* URLs to direct download URLs.

Users paste the page they are looking at — e.g.
``https://www.printables.com/model/3161-3d-benchy/files`` — rather than a
direct download link. Each host keeps the real file behind an API call keyed by
the model id embedded in the page URL. The resolvers here turn a recognised
page URL into a direct download URL that :func:`importer.download_to_staging`
can fetch; that function re-runs the SSRF guard on every hop, including the
resolved one, so resolution never bypasses the public-IP check.

Contract of :func:`resolve_page_url`:

* **Unrecognised host** (or a known host whose URL carries no model id) →
  ``None``. The caller treats the original URL as an already-direct download.
* **Recognised page that resolves** → a direct download URL string.
* **Recognised page that fails to resolve** → ``ImportError_`` with a
  host-specific code (e.g. ``printables_resolve_failed``) so the UI can tell the
  user to paste a direct link instead of silently downloading the HTML page.

The host APIs dictate the request/response shapes (that is their public
contract); everything else here — dispatch, pack selection, JSON walking,
graceful degradation — is ours.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlsplit

from app.core.http_client import get_http_client
from app.core.logging import get_logger
from app.services import browser_fetch
from app.services.importer import ImportError_

logger = get_logger(__name__)

# A browser-like UA: model hosts gate their APIs/HTML behind one.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
_TIMEOUT = 30.0

# Suffixes that mark a URL as pointing at a downloadable model/bundle.
_MODEL_EXTS = (
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

_PRINTABLES_HOSTS = {"printables.com", "www.printables.com"}
_THINGIVERSE_HOSTS = {"thingiverse.com", "www.thingiverse.com"}
_PRINTABLES_GRAPHQL = "https://api.printables.com/graphql/"


@dataclass
class ModelFile:
    """One selectable downloadable file on a model page."""

    file_id: str
    name: str
    file_type: str  # Printables DownloadFileTypeEnum: stl / gcode / sla / other
    size: Optional[int] = None


@dataclass
class CollectionMember:
    """One model belonging to a collection (its page URL + display title)."""

    page_url: str
    title: str
    source_id: str


# --------------------------------------------------------------------------- #
# Host classification + id extraction
# --------------------------------------------------------------------------- #
def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _printables_id(url: str) -> Optional[str]:
    m = re.search(r"/model/(\d+)", urlsplit(url).path)
    return m.group(1) if m else None


def _makerworld_id(url: str) -> Optional[str]:
    m = re.search(r"/models/(\d+)", urlsplit(url).path)
    return m.group(1) if m else None


def _thingiverse_id(url: str) -> Optional[str]:
    path = urlsplit(url).path
    m = re.search(r"thing:(\d+)", path) or re.search(r"/things/(\d+)", path)
    return m.group(1) if m else None


def _collection_id(url: str) -> Optional[str]:
    m = re.search(r"/collections/(\d+)", urlsplit(url).path)
    return m.group(1) if m else None


def classify_collection(url: str) -> Optional[str]:
    """Return the resolver name for a known *collection* URL, else ``None``.

    Printables (``/@user/collections/<id>``) and MakerWorld
    (``/collections/<id>-slug``) both carry the id under ``/collections/<id>``.
    """
    host = _host(url)
    if host in _PRINTABLES_HOSTS and _collection_id(url):
        return "printables"
    if (host == "makerworld.com" or host.endswith(".makerworld.com")) and _collection_id(url):
        return "makerworld"
    return None


def classify_page(url: str) -> Optional[str]:
    """Return the resolver name for a known model *page*, else ``None``.

    A host is only "known" when we can also pull a model id from the path, so a
    direct ``files.printables.com`` blob URL (different host, no ``/model/<id>``)
    is correctly treated as a direct download rather than a page.
    """
    host = _host(url)
    if host in _PRINTABLES_HOSTS and _printables_id(url):
        return "printables"
    if (host == "makerworld.com" or host.endswith(".makerworld.com")) and _makerworld_id(url):
        return "makerworld"
    if host in _THINGIVERSE_HOSTS and _thingiverse_id(url):
        return "thingiverse"
    return None


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #
def _looks_like_download(url: str) -> bool:
    lower = url.split("?", 1)[0].lower()
    return lower.endswith(_MODEL_EXTS) or "/download" in url.lower()


def _first_download_url(data: Any) -> Optional[str]:
    """Walk a JSON structure breadth-first for the first plausible file URL.

    Direct ``url``/``downloadUrl`` keys win; otherwise any string value that
    looks like a model download. Returns ``None`` if nothing qualifies.
    """
    stack: list[Any] = [data]
    fallback: Optional[str] = None
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
                and _looks_like_download(current)
            ):
                fallback = current
    return fallback


# Markers Cloudflare embeds in its "Verify you are human" interstitial. We only
# treat a page as challenged when the data we actually need (``__NEXT_DATA__``)
# is absent, so a real page that merely mentions one of these strings is safe.
_CHALLENGE_MARKERS = (
    "just a moment",
    "challenge-platform",
    "cf-chl",
    "verifying you are human",
    "/cdn-cgi/challenge-platform/",
)


def _looks_like_challenge(html: str) -> bool:
    """True when ``html`` is a Cloudflare bot-challenge page rather than content."""
    if "__NEXT_DATA__" in html:
        return False
    lowered = html.lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)


def _extract_next_data(html: str) -> Optional[Any]:
    """Pull the Next.js ``__NEXT_DATA__`` JSON blob out of a page's HTML."""
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        return None
    try:
        return json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------- #
# Printables (GraphQL)
# --------------------------------------------------------------------------- #
_PRINTABLES_META_QUERY = """
query ($id: ID!) {
  print(id: $id) {
    id
    downloadPacks { id fileType }
    stls { id name }
  }
}
"""

_PRINTABLES_LINK_MUTATION = """
mutation ($printId: ID!, $source: DownloadSourceEnum!, $fileType: DownloadFileTypeEnum, $id: ID, $files: [DownloadFileInput!]) {
  getDownloadLink(printId: $printId, source: $source, fileType: $fileType, id: $id, files: $files) {
    ok
    output { link files { link } }
  }
}
"""


async def _printables_graphql(query: str, variables: dict, referer: str) -> Any:
    client = get_http_client()
    resp = await client.post(
        _PRINTABLES_GRAPHQL,
        json={"query": query, "variables": variables},
        headers={
            "User-Agent": _BROWSER_UA,
            "Accept": "application/json",
            "Origin": "https://www.printables.com",
            "Referer": referer,
        },
        timeout=_TIMEOUT,
    )
    if resp.status_code in (401, 403, 429):
        raise ImportError_("printables_blocked")
    resp.raise_for_status()
    return resp.json()


def _pick_printables_pack(packs: Any) -> Optional[str]:
    """Prefer the all-model-files pack; fall back to any pack with an id."""
    if not isinstance(packs, list):
        return None
    for pack in packs:
        if isinstance(pack, dict) and pack.get("fileType") == "MODEL_FILES" and pack.get("id"):
            return str(pack["id"])
    for pack in packs:
        if isinstance(pack, dict) and pack.get("id"):
            return str(pack["id"])
    return None


def _printables_link_from_output(payload: Any) -> Optional[str]:
    result = (payload or {}).get("data", {}).get("getDownloadLink") or {}
    output = result.get("output") or {}
    if isinstance(output.get("link"), str):
        return output["link"]
    for entry in output.get("files") or []:
        if isinstance(entry, dict) and isinstance(entry.get("link"), str):
            return entry["link"]
    return None


async def _resolve_printables(url: str) -> Optional[str]:
    print_id = _printables_id(url)
    if not print_id:
        return None
    meta = await _printables_graphql(_PRINTABLES_META_QUERY, {"id": print_id}, url)
    print_obj = (meta or {}).get("data", {}).get("print")
    if not isinstance(print_obj, dict):
        return None

    pack_id = _pick_printables_pack(print_obj.get("downloadPacks"))
    if pack_id:
        payload = await _printables_graphql(
            _PRINTABLES_LINK_MUTATION,
            {"printId": print_id, "source": "model_detail", "fileType": "pack", "id": pack_id},
            url,
        )
        link = _printables_link_from_output(payload)
        if link:
            return link

    stl_ids = [
        str(s["id"])
        for s in (print_obj.get("stls") or [])
        if isinstance(s, dict) and s.get("id")
    ]
    if stl_ids:
        payload = await _printables_graphql(
            _PRINTABLES_LINK_MUTATION,
            {
                "printId": print_id,
                "source": "model_detail",
                "files": [{"fileType": "stl", "ids": stl_ids}],
            },
            url,
        )
        link = _printables_link_from_output(payload)
        if link:
            return link
    return None


# Printables exposes downloadable files in per-type buckets on the `print` type;
# each bucket maps to a value of DownloadFileTypeEnum used by the link mutation.
_PRINTABLES_FILES_QUERY = """
query ($id: ID!) {
  print(id: $id) {
    id
    name
    stls { id name fileSize }
    gcodes { id name fileSize }
    slas { id name fileSize }
    otherFiles { id name fileSize }
  }
}
"""

_PRINTABLES_FILE_CATEGORIES = (
    ("stls", "stl"),
    ("gcodes", "gcode"),
    ("slas", "sla"),
    ("otherFiles", "other"),
)


def _printables_files_from_print(print_obj: dict) -> list[ModelFile]:
    files: list[ModelFile] = []
    for field, file_type in _PRINTABLES_FILE_CATEGORIES:
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


async def _list_printables_files(url: str) -> Optional[tuple[str, list[ModelFile]]]:
    print_id = _printables_id(url)
    if not print_id:
        return None
    meta = await _printables_graphql(_PRINTABLES_FILES_QUERY, {"id": print_id}, url)
    print_obj = (meta or {}).get("data", {}).get("print")
    if not isinstance(print_obj, dict):
        return None
    title = str(print_obj.get("name") or print_id)
    return title, _printables_files_from_print(print_obj)


def _printables_links_from_output(payload: Any) -> list[str]:
    """All per-file links from a getDownloadLink payload (else the single link)."""
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


async def _printables_download_links(url: str, files: list[ModelFile]) -> list[str]:
    """Resolve direct download links for a chosen subset of a model's files."""
    print_id = _printables_id(url)
    if not print_id or not files:
        return []
    grouped: dict[str, list[str]] = {}
    for f in files:
        grouped.setdefault(f.file_type, []).append(f.file_id)
    files_arg = [{"fileType": file_type, "ids": ids} for file_type, ids in grouped.items()]
    payload = await _printables_graphql(
        _PRINTABLES_LINK_MUTATION,
        {"printId": print_id, "source": "model_detail", "files": files_arg},
        url,
    )
    return _printables_links_from_output(payload)


# Collection name + paginated member list. `moreCollectionModels` requires an
# explicit ordering (its server-side default errors), and returns items whose
# real print lives under `item.print`.
_PRINTABLES_COLLECTION_QUERY = """
query ($id: ID!) { collection(id: $id) { id name } }
"""

_PRINTABLES_COLLECTION_MODELS_QUERY = """
query ($collectionId: ID!, $limit: Int, $cursor: String, $ordering: CollectionPrintsOrderingEnum) {
  moreCollectionModels(collectionId: $collectionId, limit: $limit, cursor: $cursor, ordering: $ordering) {
    cursor
    items { id print { id name } }
  }
}
"""


async def _resolve_printables_collection(url: str) -> Optional[tuple[str, list[CollectionMember]]]:
    collection_id = _collection_id(url)
    if not collection_id:
        return None
    meta = await _printables_graphql(_PRINTABLES_COLLECTION_QUERY, {"id": collection_id}, url)
    collection = (meta or {}).get("data", {}).get("collection") or {}
    title = str(collection.get("name") or f"Collection {collection_id}")

    members: list[CollectionMember] = []
    seen: set[str] = set()
    cursor: Optional[str] = None
    for _ in range(50):  # safety cap: 50 pages * 50 = 2500 members
        data = await _printables_graphql(
            _PRINTABLES_COLLECTION_MODELS_QUERY,
            {
                "collectionId": collection_id,
                "limit": 50,
                "cursor": cursor,
                "ordering": "added_to_collection",
            },
            url,
        )
        block = (data or {}).get("data", {}).get("moreCollectionModels") or {}
        items = block.get("items") or []
        for item in items:
            print_obj = (item or {}).get("print") or {}
            print_id = print_obj.get("id") or (item or {}).get("id")
            if not print_id or str(print_id) in seen:
                continue
            seen.add(str(print_id))
            members.append(
                CollectionMember(
                    page_url=f"https://www.printables.com/model/{print_id}",
                    title=str(print_obj.get("name") or print_id),
                    source_id=str(print_id),
                )
            )
        cursor = block.get("cursor")
        if not cursor or not items:
            break
    return title, members


# --------------------------------------------------------------------------- #
# MakerWorld (Next.js page → instance/model download API)
# --------------------------------------------------------------------------- #
def _makerworld_api_headers(referer: str, nonce: Optional[str]) -> dict:
    # The login cookie is injected into the browser context by browser_fetch, not
    # set here, so the request carries it alongside Cloudflare clearance.
    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "application/json",
        "X-BBL-Client-Type": "web",
        "X-BBL-Client-Name": "MakerWorld",
        "Referer": referer,
    }
    if nonce:
        headers["X-Nonce"] = nonce
    return headers


async def _makerworld_fetch_page(url: str, cookie: Optional[str]) -> Optional[str]:
    """Fetch a MakerWorld page's HTML, rendering past Cloudflare if needed.

    The cheap httpx fetch is tried first. If MakerWorld returns nothing usable —
    a non-200, or the Cloudflare "Verify you are human" interstitial — fall back
    to a headless browser that solves the challenge and returns rendered HTML.
    """
    client = get_http_client()
    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if cookie:
        headers["Cookie"] = cookie
    resp = await client.get(url, headers=headers, follow_redirects=True, timeout=_TIMEOUT)
    html = resp.text if resp.status_code == 200 else None

    if html is not None and not _looks_like_challenge(html):
        return html

    # httpx was blocked or challenged — render the page in a fresh browser context
    # that solves the Cloudflare challenge.
    rendered = await browser_fetch.fetch_rendered_html(
        url, wait_selector="script#__NEXT_DATA__", extra_cookie=cookie
    )
    if rendered:
        return rendered
    return html


async def _makerworld_api_get(
    api_url: str, referer: str, nonce: Optional[str], cookie: Optional[str]
) -> Optional[Any]:
    """GET a MakerWorld API endpoint through the browser, past Cloudflare.

    Plain httpx is challenged (403) by Cloudflare, so the request rides the
    browser's request context. ``cookie`` carries the user's login session;
    download endpoints are auth-gated and answer 403 "please log in" without it.
    """
    headers = _makerworld_api_headers(referer, nonce)
    result = await browser_fetch.api_get(api_url, cookie=cookie, headers=headers)
    if result is None:
        return None
    status, text = result
    if status in (401, 403, 429):
        if "log in" in text.lower() or "login" in text.lower():
            raise ImportError_("makerworld_login_required")
        raise ImportError_("makerworld_blocked")
    if status != 200:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


async def _resolve_makerworld(url: str, cookie: Optional[str]) -> Optional[str]:
    """Resolve a MakerWorld model page to a direct download URL.

    The page HTML embeds no real file link (only thumbnails and store links), so
    we go straight to the download API: the public design endpoint yields the
    instance id, and the instance's ``f3mf`` endpoint yields the file link. The
    latter is auth-gated — without a login ``cookie`` it raises
    ``makerworld_login_required`` (surfaced via :func:`_makerworld_api_get`).
    """
    design_id = _makerworld_id(url)
    if not design_id:
        return None
    base = "https://makerworld.com/api/v1/design-service"

    instance_id: Optional[str] = None
    design = await _makerworld_api_get(f"{base}/design/{design_id}", url, None, cookie)
    if isinstance(design, dict):
        if design.get("defaultInstanceId"):
            instance_id = str(design["defaultInstanceId"])
        else:
            for inst in design.get("instances") or []:
                if isinstance(inst, dict) and inst.get("id"):
                    instance_id = str(inst["id"])
                    break

    if instance_id:
        api = f"{base}/instance/{instance_id}/f3mf?type=download&fileType=3mfstl"
        data = await _makerworld_api_get(api, url, None, cookie)
        link = _first_download_url(data) if data is not None else None
        if link:
            return link

    # Fallback to the model-level download endpoint.
    api = f"https://makerworld.com/api/v1/models/{design_id}/download"
    data = await _makerworld_api_get(api, url, None, cookie)
    return _first_download_url(data) if data is not None else None


def _makerworld_collection_members(next_data: Any) -> list[CollectionMember]:
    """Best-effort: pull member models out of a collection page's hydration JSON.

    MakerWorld is Cloudflare-gated and ships no public collection API, so we walk
    ``__NEXT_DATA__`` for lists of design-like objects (an id + a title). The exact
    shape is not contract-guaranteed; this degrades to an empty list if the page
    cannot be parsed (the caller then reports ``*_collection_resolve_failed``).
    """
    try:
        props = next_data["props"]["pageProps"]
    except (KeyError, TypeError):
        return []

    members: list[CollectionMember] = []
    seen: set[str] = set()

    def consider(entry: Any) -> None:
        if not isinstance(entry, dict):
            return
        design = entry.get("design") if isinstance(entry.get("design"), dict) else entry
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

    # MakerWorld embeds members under e.g. ``favoriteDesigns.hits`` / ``designs``.
    _MEMBER_LIST_HINTS = (
        "design", "model", "content", "hit", "item", "list", "record", "favorite",
    )

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(value, list) and any(h in key.lower() for h in _MEMBER_LIST_HINTS):
                    for entry in value:
                        consider(entry)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(props)
    return members


async def _resolve_makerworld_collection(
    url: str, cookie: Optional[str]
) -> Optional[tuple[str, list[CollectionMember]]]:
    collection_id = _collection_id(url)
    if not collection_id:
        return None
    html = await _makerworld_fetch_page(url, cookie)
    if not html:
        return None
    next_data = _extract_next_data(html)
    if next_data is None:
        return None

    title = f"Collection {collection_id}"
    try:
        props = next_data["props"]["pageProps"]
        # MakerWorld renamed collections to "favorites"; older pages used "collection".
        meta = props.get("favorite") or props.get("collection") or {}
        if isinstance(meta, dict) and (meta.get("title") or meta.get("name")):
            title = str(meta.get("title") or meta.get("name"))
    except (KeyError, TypeError):
        pass

    members = _makerworld_collection_members(next_data)
    return (title, members) if members else None


# --------------------------------------------------------------------------- #
# Thingiverse (stable public per-thing zip endpoint)
# --------------------------------------------------------------------------- #
async def _resolve_thingiverse(url: str, cookie: Optional[str]) -> Optional[str]:
    thing_id = _thingiverse_id(url)
    if not thing_id:
        return None
    # Public things expose every file as one zip at this stable URL; it
    # 302-redirects to a CDN blob that ``download_to_staging`` follows. No API
    # token needed for public models, so we prefer it over the token dance.
    return f"https://www.thingiverse.com/thing:{thing_id}/zip"


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
async def resolve_page_url(
    url: str,
    *,
    makerworld_cookie: Optional[str] = None,
    thingiverse_cookie: Optional[str] = None,
) -> Optional[str]:
    """Resolve a known model *page* URL to a direct download URL (see module doc)."""
    kind = classify_page(url)
    if kind is None:
        return None

    try:
        if kind == "printables":
            resolved = await _resolve_printables(url)
        elif kind == "makerworld":
            resolved = await _resolve_makerworld(url, makerworld_cookie)
        else:
            resolved = await _resolve_thingiverse(url, thingiverse_cookie)
    except ImportError_:
        raise
    except Exception as exc:  # noqa: BLE001 — network/parse boundary
        logger.warning("page resolution errored for %s: %s", url, exc)
        raise ImportError_(f"{kind}_resolve_failed") from exc

    if not resolved:
        raise ImportError_(f"{kind}_resolve_failed")
    return resolved


async def list_model_files(url: str) -> Optional[tuple[str, list[ModelFile]]]:
    """List a model page's selectable files without downloading anything.

    Printables-only (its API enumerates files cheaply). Returns ``(title, files)``
    or ``None`` for any other host, so the caller falls back to resolve+download.
    """
    if classify_page(url) != "printables":
        return None
    try:
        return await _list_printables_files(url)
    except ImportError_:
        raise
    except Exception as exc:  # noqa: BLE001 — network/parse boundary
        logger.warning("file listing errored for %s: %s", url, exc)
        raise ImportError_("printables_resolve_failed") from exc


async def resolve_selected_download(url: str, files: list[ModelFile]) -> list[str]:
    """Resolve direct download links for a user-chosen subset of a page's files."""
    if classify_page(url) != "printables":
        raise ImportError_("file_selection_unsupported")
    try:
        links = await _printables_download_links(url, files)
    except ImportError_:
        raise
    except Exception as exc:  # noqa: BLE001 — network/parse boundary
        logger.warning("selected download errored for %s: %s", url, exc)
        raise ImportError_("printables_resolve_failed") from exc
    if not links:
        raise ImportError_("printables_resolve_failed")
    return links


async def resolve_collection_url(
    url: str, *, makerworld_cookie: Optional[str] = None
) -> Optional[tuple[str, list[CollectionMember]]]:
    """Resolve a collection URL to ``(title, members)``; ``None`` if not a collection."""
    kind = classify_collection(url)
    if kind is None:
        return None

    try:
        if kind == "printables":
            resolved = await _resolve_printables_collection(url)
        else:
            resolved = await _resolve_makerworld_collection(url, makerworld_cookie)
    except ImportError_:
        raise
    except Exception as exc:  # noqa: BLE001 — network/parse boundary
        logger.warning("collection resolution errored for %s: %s", url, exc)
        raise ImportError_(f"{kind}_collection_resolve_failed") from exc

    if not resolved or not resolved[1]:
        raise ImportError_(f"{kind}_collection_resolve_failed")
    return resolved
