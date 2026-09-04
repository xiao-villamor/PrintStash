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

from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from time import monotonic
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from printstash_core.imports import (
    CaptureContractError,
    CaptureManifestV2,
    ResolvedAsset,
    canonicalize_provider_url,
)
from printstash_core.imports import resolvers as _resolver_rules

from app.core.logging import get_logger
from app.core.metrics import record_capture_operation
from app.core.time import utcnow
from app.db.session import SessionFactory
from app.services import provider_connections
from app.services.capture_provider_connections import ProviderModelMetadata
from app.services.capture_provider_transport import (
    ProviderTransport,
    ProviderTransportError,
)
from app.services.importer import ImportError_
from app.services.provider_redaction import redact_exception, redact_url

logger = get_logger(__name__)


@dataclass(frozen=True)
class ProviderResolutionContext:
    owner_user_id: int
    session_factory: SessionFactory


_provider_metadata_cache: OrderedDict[
    tuple[int, str, str], tuple[ProviderModelMetadata, datetime]
] = OrderedDict()
_PROVIDER_CACHE_MAX = 256
_PROVIDER_CACHE_TTL = timedelta(minutes=5)


def invalidate_provider_metadata_cache(owner_user_id: int, provider: str) -> None:
    """Drop all cached metadata for one user's provider connection."""
    for key in tuple(_provider_metadata_cache):
        if key[0] == owner_user_id and key[1] == provider:
            _provider_metadata_cache.pop(key, None)


def _provider_capture_fields(
    metadata: ProviderModelMetadata,
) -> dict[str, dict[str, str]]:
    """Map the provider metadata allowlist into the capture field vocabulary.

    ``ProviderModelMetadata`` deliberately carries only normalized metadata;
    credentials, request headers, and transient file URLs are not part of its
    shape.  Keep this mapping explicit so a future provider response widening
    cannot leak an unknown field into the persisted manifest.
    """
    fields: dict[str, dict[str, str]] = {
        "title": {"value": metadata.title, "origin": "confirmed"}
    }
    for manifest_name, value in (
        ("description", metadata.description),
        ("creator_name", metadata.creator),
        # The provider adapter exposes a human-readable license name, not a
        # code or URL, so retain it as license text without inventing data.
        ("license_text", metadata.license_name),
    ):
        if isinstance(value, str) and value:
            fields[manifest_name] = {"value": value, "origin": "confirmed"}
    return fields


def _canonical_provider_page(provider: str, url: str) -> tuple[str, str]:
    """Normalize one provider page and return its route item identity.

    Canonical evidence is metadata, not a bearer URL.  Do not silently drop
    query/fragment/userinfo data at this server-side identity boundary.
    """
    try:
        raw = urlsplit(url)
        if (
            raw.scheme.lower() != "https"
            or raw.username is not None
            or raw.password is not None
            or raw.query
            or raw.fragment
            or raw.port not in {None, 443}
        ):
            raise ValueError
        canonical = canonicalize_provider_url(provider, url)
        parsed = urlsplit(canonical)
        canonical_host = parsed.hostname or ""
        if provider == "myminifactory":
            canonical_host = "www.myminifactory.com"
        elif provider == "cults":
            canonical_host = "cults3d.com"
        canonical = urlunsplit((parsed.scheme, canonical_host, parsed.path, "", ""))
        parsed = urlsplit(canonical)
        parts = [part for part in parsed.path.split("/") if part]
        if not parts:
            raise ValueError
        return canonical, parts[-1]
    except (AttributeError, TypeError, ValueError, CaptureContractError):
        raise ImportError_("provider_contract_changed") from None


def _provider_identity_matches(
    metadata: ProviderModelMetadata,
    provider: str,
    submitted_url: str,
    source_item_id: str,
) -> bool:
    """Prove response identity against the submitted host, route, and item."""
    identity = metadata.identity
    if identity is None or identity.canonical_url is None:
        return False
    try:
        submitted_canonical, submitted_item = _canonical_provider_page(
            provider, submitted_url
        )
        response_canonical, response_item = _canonical_provider_page(
            provider, identity.canonical_url
        )
    except ImportError_:
        return False
    if response_canonical != submitted_canonical or response_item != submitted_item:
        return False
    if provider == "cults":
        # Cults API IDs are intentionally opaque. The canonical path/slug is
        # the item binding; the adapter's explicit canonical_slug is a second
        # independent check against a response that only echoes a URL.
        return identity.canonical_slug == submitted_item
    # MMF's API ID must bind to the exact /object/{id} route as well as the
    # provider-returned canonical URL. A tail-only provider_id is insufficient.
    return identity.provider_id == submitted_item


async def resolve_connected_provider_capture(
    url: str, context: ProviderResolutionContext
) -> CaptureManifestV2 | None:
    """Resolve credentialed provider metadata without exposing credentials or URLs."""
    try:
        provider = classify_page(url)
    except Exception:
        raise ImportError_("provider_contract_changed") from None
    if provider not in {"myminifactory", "cults"}:
        return None
    started = monotonic()
    canonical_url, source_item_id = _canonical_provider_page(provider, url)
    if not source_item_id:
        raise ImportError_("provider_contract_changed")
    key = (context.owner_user_id, provider, source_item_id)
    cached = _provider_metadata_cache.get(key)
    if cached is not None and cached[1] > utcnow():
        provider_enum = (
            provider_connections.CaptureProvider.CULTS
            if provider == "cults"
            else provider_connections.CaptureProvider.MYMINIFACTORY
        )
        with context.session_factory.scoped_session() as session:
            active = provider_connections.has_active_provider_connection(
                session, context.owner_user_id, provider_enum
            )
        if active:
            metadata = cached[0]
            _provider_metadata_cache.move_to_end(key)
        else:
            _provider_metadata_cache.pop(key, None)
            cached = None
    else:
        cached = None
    if cached is None:
        try:
            with context.session_factory.scoped_session() as session:
                if provider == "myminifactory":
                    metadata = await provider_connections.fetch_mmf_model_metadata(
                        session, context.owner_user_id, source_item_id
                    )
                else:
                    metadata = await provider_connections.fetch_cults_model_metadata(
                        session, context.owner_user_id, source_item_id
                    )
        except provider_connections.ProviderConnectionError as exc:
            if exc.code in {"provider_not_connected", "provider_connection_invalid"}:
                record_capture_operation(
                    provider,
                    "provider_api",
                    "required",
                    monotonic() - started,
                    error_category="provider_connection_required",
                )
                raise ImportError_("provider_connection_required") from None
            category = (
                "provider_rate_limited"
                if exc.retryable
                else "provider_contract_changed"
            )
            record_capture_operation(
                provider,
                "provider_api",
                "rate_limited" if exc.retryable else "contract_changed",
                monotonic() - started,
                error_category=category,
            )
            raise ImportError_(category) from None
        except Exception:
            # Provider adapters are an untrusted contract boundary.  Never
            # expose parser details, response text, or credential-bearing
            # exception messages to Inbox/API callers.
            record_capture_operation(
                provider,
                "provider_api",
                "contract_changed",
                monotonic() - started,
                error_category="provider_contract_changed",
            )
            raise ImportError_("provider_contract_changed") from None
    try:
        if not _provider_identity_matches(
            metadata, provider, canonical_url, source_item_id
        ):
            raise CaptureContractError("provider model identity changed")
        files = [
            {
                "id": file.file_id,
                "name": file.name,
                "file_type": file.name.rsplit(".", 1)[-1].lower(),
                "size": file.size_bytes,
            }
            for file in metadata.files
            if "." in file.name
        ]
        if not files:
            # Cults deliberately returns metadata only; the browser supplies bytes.
            files = [
                {
                    "id": source_item_id,
                    "name": f"{source_item_id}.3mf",
                    "file_type": "3mf",
                    "size": None,
                }
            ]
        manifest = CaptureManifestV2.from_dict(
            {
                "schema_version": 2,
                "kind": "model_files",
                "source": {
                    "provider": provider,
                    "canonical_url": canonical_url,
                    "source_item_id": metadata.model_id,
                    "source_revision": None,
                    "adapter_version": "provider-api-v1",
                    "fields": _provider_capture_fields(metadata),
                    "tags": list(metadata.tags),
                },
                "files": files,
                "selected_ids": [file["id"] for file in files],
            }
        )
    except Exception:
        # Keep malformed metadata out of the owner-scoped cache as well as the
        # persisted Inbox manifest.  Only the stable category crosses the seam.
        _provider_metadata_cache.pop(key, None)
        record_capture_operation(
            provider,
            "provider_api",
            "contract_changed",
            monotonic() - started,
            error_category="provider_contract_changed",
        )
        raise ImportError_("provider_contract_changed") from None

    if cached is None or cached[1] <= utcnow():
        _provider_metadata_cache[key] = (metadata, utcnow() + _PROVIDER_CACHE_TTL)
        _provider_metadata_cache.move_to_end(key)
        while len(_provider_metadata_cache) > _PROVIDER_CACHE_MAX:
            _provider_metadata_cache.popitem(last=False)
    record_capture_operation(provider, "provider_api", "success", monotonic() - started)
    return manifest


# A browser-like UA: model hosts gate their APIs/HTML behind one.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
_TIMEOUT = 30.0

_PRINTABLES_GRAPHQL = "https://api.printables.com/graphql/"
_PRINTABLES_ALLOWED_HOSTS = frozenset({"api.printables.com"})

# Compatibility aliases preserve the OSS resolver module's existing API and
# patch points while delegating deterministic rules to the shared core.
ModelFile = _resolver_rules.ModelFile
CollectionMember = _resolver_rules.CollectionMember
_MODEL_EXTS = _resolver_rules.MODEL_EXTENSIONS
_PRINTABLES_HOSTS = _resolver_rules.PRINTABLES_HOSTS
_THINGIVERSE_HOSTS = _resolver_rules.THINGIVERSE_HOSTS
_CHALLENGE_MARKERS = _resolver_rules.CHALLENGE_MARKERS
_PRINTABLES_FILE_CATEGORIES = _resolver_rules.PRINTABLES_FILE_CATEGORIES
_host = _resolver_rules.host
_printables_id = _resolver_rules.printables_id
_makerworld_id = _resolver_rules.makerworld_id
_thingiverse_id = _resolver_rules.thingiverse_id
_collection_id = _resolver_rules.collection_id
classify_collection = _resolver_rules.classify_collection


def classify_page(url: str) -> str | None:
    """Classify public model pages, including credentialed capture providers."""
    known = _resolver_rules.classify_page(url)
    if known is not None:
        return known
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = parts.path.strip("/").split("/")
    if host == "myminifactory.com" and len(path) >= 2 and path[0] == "object":
        return "myminifactory"
    if host == "cults3d.com" and "3d-model" in path:
        return "cults"
    return None


_looks_like_download = _resolver_rules.looks_like_download
_first_download_url = _resolver_rules.first_download_url
_looks_like_challenge = _resolver_rules.looks_like_challenge
_extract_next_data = _resolver_rules.extract_next_data
_pick_printables_pack = _resolver_rules.pick_printables_pack
_printables_link_from_output = _resolver_rules.printables_link_from_output
_printables_files_from_print = _resolver_rules.printables_files_from_print
_printables_links_from_output = _resolver_rules.printables_links_from_output
_makerworld_instance_id = _resolver_rules.makerworld_instance_id
_makerworld_collection_title = _resolver_rules.makerworld_collection_title
_makerworld_collection_members = _resolver_rules.makerworld_collection_members
parse_printables_capture = _resolver_rules.parse_printables_capture


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
    output { link files { id fileId link } }
  }
}
"""


async def _printables_graphql(query: str, variables: dict, referer: str) -> Any:
    """Request Printables metadata through the bounded provider boundary.

    Keep this adapter deliberately thin: GraphQL payloads and response status
    handling remain compatible with the old client, while DNS pinning,
    redirects, retries, response limits, and host concurrency belong to
    :class:`ProviderTransport`.
    """
    response = None
    try:
        response = await ProviderTransport().request(
            "POST",
            _PRINTABLES_GRAPHQL,
            json={"query": query, "variables": variables},
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "application/json",
                "Origin": "https://www.printables.com",
                "Referer": referer,
            },
            allowed_hosts=_PRINTABLES_ALLOWED_HOSTS,
        )
        if response.status_code in (401, 403, 429):
            raise ImportError_("printables_blocked")
        response.raise_for_status()
        return response.json()
    except ImportError_:
        raise
    except ProviderTransportError as exc:
        # Transport exceptions carry only a stable code.  Do not expose their
        # cause or upstream response text to the resolver/API boundary.
        if exc.status_code == 429:
            raise ImportError_("printables_blocked") from None
        logger.warning(
            "Printables GraphQL transport failed for %s: %s",
            redact_url(referer),
            exc.code,
        )
        raise ImportError_("printables_resolve_failed") from None
    except Exception as exc:  # noqa: BLE001 — provider boundary
        logger.warning(
            "Printables GraphQL request failed for %s: %s",
            redact_url(referer),
            redact_exception(exc),
        )
        raise ImportError_("printables_resolve_failed") from None
    finally:
        if response is not None:
            response.close()


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
            {
                "printId": print_id,
                "source": "model_detail",
                "fileType": "pack",
                "id": pack_id,
            },
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
    title
    user { name username }
    license { name code }
    stls { id name fileSize }
    gcodes { id name fileSize }
    slas { id name fileSize }
    otherFiles { id name fileSize }
  }
}
"""


async def resolve_capture_manifest(url: str) -> CaptureManifestV2 | None:
    """Return the bounded V2 capture manifest for a supported public page.

    Download links are intentionally absent: they are short-lived transport
    data resolved only after the user has chosen files to import.
    """
    if classify_page(url) != "printables":
        return None
    print_id = _printables_id(url)
    if not print_id:
        return None
    try:
        payload = await _printables_graphql(
            _PRINTABLES_FILES_QUERY, {"id": print_id}, url
        )
        return parse_printables_capture(payload, url)
    except ImportError_:
        raise
    except CaptureContractError as exc:
        logger.warning(
            "Printables capture contract failed for %s: %s",
            redact_url(url),
            redact_exception(exc),
        )
        raise ImportError_("printables_capture_invalid") from exc
    except Exception as exc:  # noqa: BLE001 — provider boundary
        logger.warning(
            "Printables capture failed for %s: %s",
            redact_url(url),
            redact_exception(exc),
        )
        raise ImportError_("printables_resolve_failed") from exc


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


async def _printables_download_links(url: str, files: list[ModelFile]) -> list[str]:
    """Resolve direct download links for a chosen subset of a model's files."""
    print_id = _printables_id(url)
    if not print_id or not files:
        return []
    grouped: dict[str, list[str]] = {}
    for f in files:
        grouped.setdefault(f.file_type, []).append(f.file_id)
    files_arg = [
        {"fileType": file_type, "ids": ids} for file_type, ids in grouped.items()
    ]
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


async def _resolve_printables_collection(
    url: str,
) -> Optional[tuple[str, list[CollectionMember]]]:
    collection_id = _collection_id(url)
    if not collection_id:
        return None
    meta = await _printables_graphql(
        _PRINTABLES_COLLECTION_QUERY, {"id": collection_id}, url
    )
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
            raise ImportError_("makerworld_extension_required")
        else:
            # Thingiverse metadata and file choices are browser-visible only.
            # Do not request its page or its legacy per-thing ZIP endpoint;
            # browser capture hands the user's explicit file selection back to
            # the pending-import flow.
            raise ImportError_("thingiverse_extension_required")
    except ImportError_:
        raise
    except Exception as exc:  # noqa: BLE001 — network/parse boundary
        logger.warning(
            "page resolution errored for %s: %s",
            redact_url(url),
            redact_exception(exc),
        )
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
        logger.warning(
            "file listing errored for %s: %s",
            redact_url(url),
            redact_exception(exc),
        )
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
        logger.warning(
            "selected download errored for %s: %s",
            redact_url(url),
            redact_exception(exc),
        )
        raise ImportError_("printables_resolve_failed") from exc
    if not links:
        raise ImportError_("printables_resolve_failed")
    return links


async def resolve_selected_assets(
    url: str,
    manifest: CaptureManifestV2,
    selected_ids: list[str],
    context: ProviderResolutionContext | None = None,
) -> list[ResolvedAsset]:
    """Resolve V2 selections by provider file ID, never response position.

    A provider response that omits, duplicates, or substitutes an ID is unsafe:
    it could attach an Artifact to the wrong captured file, so it fails closed.
    """
    if classify_page(url) == "myminifactory":
        if context is None:
            raise ImportError_("provider_connection_required")
        wanted = selected_ids or list(manifest.selected_ids)
        files = {file.id: file for file in manifest.files}
        try:
            with context.session_factory.scoped_session() as session:
                links = {
                    file_id: await provider_connections.fetch_mmf_file_download_url(
                        session, context.owner_user_id, file_id
                    )
                    for file_id in wanted
                }
        except provider_connections.ProviderConnectionError as exc:
            raise ImportError_(
                "provider_rate_limited"
                if exc.retryable
                else "provider_connection_required"
            ) from None
        return [
            ResolvedAsset(
                manifest=manifest,
                source_selection_id=file_id,
                source_file_id=file_id,
                source_filename=files[file_id].name,
                download_url=links[file_id],
                source_item_id=manifest.source.source_item_id or "",
            )
            for file_id in wanted
        ]
    if classify_page(url) == "cults":
        raise ImportError_("user_file_required")
    if classify_page(url) != "printables":
        raise ImportError_("file_selection_unsupported")
    wanted = selected_ids or list(manifest.selected_ids)
    capture_files = {file.id: file for file in manifest.files}
    if len(set(wanted)) != len(wanted) or not set(wanted) <= set(capture_files):
        raise ImportError_("file_selection_invalid")
    try:
        payload = await _printables_graphql(
            _PRINTABLES_LINK_MUTATION,
            {
                "printId": manifest.source.source_item_id,
                "source": "model_detail",
                "files": [
                    {
                        "fileType": capture_files[file_id].file_type,
                        "ids": [file_id],
                    }
                    for file_id in wanted
                ],
            },
            url,
        )
        output = (payload.get("data") or {}).get("getDownloadLink", {}).get(
            "output"
        ) or {}
        response_files = output.get("files") or []
        links_by_id: dict[str, str] = {}
        for entry in response_files:
            if not isinstance(entry, dict):
                raise ImportError_("printables_resolve_failed")
            file_id = entry.get("id") or entry.get("fileId")
            link = entry.get("link")
            if (
                not isinstance(file_id, str)
                or not isinstance(link, str)
                or file_id in links_by_id
            ):
                raise ImportError_("printables_resolve_failed")
            links_by_id[file_id] = link
        if set(links_by_id) != set(wanted):
            raise ImportError_("printables_resolve_failed")
    except ImportError_:
        raise
    except Exception as exc:  # noqa: BLE001 — provider boundary
        logger.warning(
            "selected asset resolution errored for %s: %s",
            redact_url(url),
            redact_exception(exc),
        )
        raise ImportError_("printables_resolve_failed") from exc
    return [
        ResolvedAsset(
            manifest=manifest,
            source_selection_id=file_id,
            source_file_id=file_id,
            source_filename=capture_files[file_id].name,
            download_url=links_by_id[file_id],
            source_item_id=manifest.source.source_item_id or "",
        )
        for file_id in wanted
    ]


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
            raise ImportError_("makerworld_extension_required")
    except ImportError_:
        raise
    except Exception as exc:  # noqa: BLE001 — network/parse boundary
        logger.warning(
            "collection resolution errored for %s: %s",
            redact_url(url),
            redact_exception(exc),
        )
        raise ImportError_(f"{kind}_collection_resolve_failed") from exc

    if not resolved or not resolved[1]:
        raise ImportError_(f"{kind}_collection_resolve_failed")
    return resolved
