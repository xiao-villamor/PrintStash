"""Metadata-only clients for official external provider APIs.

Connection credentials and returned bearer tokens are call arguments, never
configuration or persistence fields. This module does not download provider files.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.services.capture_provider_transport import ProviderTransportError

_MMF_API = "https://www.myminifactory.com/api/v2"
_MMF_ALLOWED_HOSTS = frozenset({"www.myminifactory.com"})
_CULTS_GRAPHQL = "https://cults3d.com/graphql"
_CULTS_ALLOWED_HOSTS = frozenset({"cults3d.com"})
_CULTS_CANONICAL_HOST = "cults3d.com"
_REMOTE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_MAX_RESPONSE_BYTES = 256 * 1024
_MAX_JSON_DEPTH = 8
_MAX_JSON_FIELDS = 64
_MAX_JSON_LIST_ITEMS = 128
_MAX_JSON_STRING_LENGTH = 16 * 1024
_MAX_PROVIDER_FILES = 100
_MAX_PROVIDER_TAGS = 50


class ProviderRequester(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        json: Any = None,
        data: Mapping[str, str] | None = None,
        auth: tuple[str, str] | None = None,
        allowed_hosts: Collection[str] | None = None,
    ) -> httpx.Response: ...


class ProviderConnectionError(Exception):
    """Stable provider-facing failure with no response body or secret data."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class MyMiniFactoryCredentials:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)


@dataclass(frozen=True)
class MyMiniFactoryTokens:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_in_seconds: int


@dataclass(frozen=True)
class ProviderFileMetadata:
    file_id: str
    name: str
    size_bytes: int | None


@dataclass(frozen=True)
class ProviderIdentity:
    """The two independent identities returned by a provider.

    ``canonical_slug``/``canonical_url`` identify the page the user supplied;
    ``provider_id`` is an opaque API identifier.  Providers are allowed to
    return different values for those dimensions (Cults does), so callers must
    never compare the opaque ID directly with a URL slug.
    """

    provider_id: str | None = None
    canonical_slug: str | None = None
    canonical_url: str | None = None

    @property
    def opaque_id(self) -> str | None:
        """Compatibility name for callers that emphasize API opacity."""
        return self.provider_id


@dataclass(frozen=True)
class ProviderModelMetadata:
    model_id: str
    title: str
    description: str | None
    creator: str | None
    license_name: str | None
    files: tuple[ProviderFileMetadata, ...] = ()
    tags: tuple[str, ...] = ()
    identity: ProviderIdentity | None = None

    def __post_init__(self) -> None:
        # Keep the legacy ``model_id`` field readable for existing consumers,
        # while making the identity dimensions explicit for new callers.
        if self.identity is None:
            object.__setattr__(
                self,
                "identity",
                ProviderIdentity(provider_id=self.model_id),
            )


class MyMiniFactoryMetadataClient:
    """Narrow official OAuth/API adapter: tokens plus model/file metadata only."""

    def __init__(self, transport: ProviderRequester) -> None:
        self._transport = transport

    async def exchange_code(
        self, credentials: MyMiniFactoryCredentials, *, code: str, redirect_uri: str
    ) -> MyMiniFactoryTokens:
        return await self._token_request(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
            }
        )

    async def refresh_tokens(
        self, credentials: MyMiniFactoryCredentials, tokens: MyMiniFactoryTokens
    ) -> MyMiniFactoryTokens:
        refreshed = await self._token_request(
            {
                "grant_type": "refresh_token",
                "refresh_token": tokens.refresh_token,
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
            }
        )
        return MyMiniFactoryTokens(
            refreshed.access_token,
            refreshed.refresh_token or tokens.refresh_token,
            refreshed.expires_in_seconds,
        )

    async def model_metadata(
        self, model_id: str, tokens: MyMiniFactoryTokens
    ) -> ProviderModelMetadata:
        _require_identifier(model_id)
        response = await self._request(
            "GET",
            f"{_MMF_API}/objects/{model_id}",
            headers={
                "Authorization": f"Bearer {tokens.access_token}",
                "Accept": "application/json",
            },
        )
        payload = _json_object(response)
        return _model_from_payload(payload, model_id)

    async def file_download_url(self, file_id: str, tokens: MyMiniFactoryTokens) -> str:
        _require_identifier(file_id)
        response = await self._request(
            "GET",
            f"{_MMF_API}/files/{file_id}",
            headers={
                "Authorization": f"Bearer {tokens.access_token}",
                "Accept": "application/json",
            },
        )
        value = _text(_json_object(response).get("download_url"))
        if not value:
            raise ProviderConnectionError("provider_response_invalid")
        _require_public_https_url(value)
        return value

    async def _token_request(self, data: dict[str, str]) -> MyMiniFactoryTokens:
        response = await self._request(
            "POST", f"{_MMF_API}/user/oauth/token", data=data
        )
        payload = _json_object(response)
        access_token = _text(payload.get("access_token"))
        refresh_token = _text(payload.get("refresh_token"))
        expires_in = payload.get("expires_in")
        if not access_token or not isinstance(expires_in, int) or expires_in <= 0:
            raise ProviderConnectionError("provider_response_invalid")
        return MyMiniFactoryTokens(access_token, refresh_token or "", expires_in)

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._transport.request(
                method, url, allowed_hosts=_MMF_ALLOWED_HOSTS, **kwargs
            )
        except ProviderTransportError as exc:
            raise ProviderConnectionError(exc.code, retryable=exc.retryable) from exc
        _raise_for_status(response)
        return response


@dataclass(frozen=True)
class CultsCredentials:
    username: str = field(repr=False)
    password: str = field(repr=False)


class CultsMetadataClient:
    """Official Cults GraphQL client restricted to metadata fields."""

    def __init__(self, transport: ProviderRequester) -> None:
        self._transport = transport

    async def validate_credentials(self, credentials: CultsCredentials) -> None:
        try:
            response = await self._transport.request(
                "POST",
                _CULTS_GRAPHQL,
                json={"query": "query { viewer { id } }", "variables": {}},
                auth=(credentials.username, credentials.password),
                allowed_hosts=_CULTS_ALLOWED_HOSTS,
            )
        except ProviderTransportError as exc:
            raise ProviderConnectionError(exc.code, retryable=exc.retryable) from exc
        _raise_for_status(response)
        _json_object(response)

    async def creation_metadata(
        self, slug: str, credentials: CultsCredentials
    ) -> ProviderModelMetadata:
        _require_identifier(slug)
        query = """
            query CreationMetadata($slug: String!) {
              creation(slug: $slug) {
                id name description url creator { nick } tags { name }
              }
            }
        """
        try:
            response = await self._transport.request(
                "POST",
                _CULTS_GRAPHQL,
                json={"query": query, "variables": {"slug": slug}},
                auth=(credentials.username, credentials.password),
                allowed_hosts=_CULTS_ALLOWED_HOSTS,
            )
        except ProviderTransportError as exc:
            raise ProviderConnectionError(exc.code, retryable=exc.retryable) from exc
        _raise_for_status(response)
        payload = _json_object(response)
        data = payload.get("data")
        creation = data.get("creation") if isinstance(data, dict) else None
        if not isinstance(creation, dict):
            raise ProviderConnectionError("provider_response_invalid")
        source_url = creation.get("url")
        if source_url is None:
            # Without canonical URL evidence, an opaque ``creation.id`` cannot
            # prove that the response belongs to the requested page.
            raise ProviderConnectionError("provider_response_invalid")
        _require_public_https_url(source_url)
        canonical_url, canonical_slug = _cults_identity_from_url(source_url)
        if canonical_slug != slug:
            # A provider response for a different canonical page is an unsafe
            # substitution even when its opaque API ID is valid.
            raise ProviderConnectionError("provider_response_invalid")
        tags_value = creation.get("tags", [])
        if not isinstance(tags_value, list) or len(tags_value) > _MAX_PROVIDER_TAGS:
            raise ProviderConnectionError("provider_response_invalid")
        return ProviderModelMetadata(
            model_id=_identifier(creation.get("id")) or slug,
            title=_text(creation.get("name")) or slug,
            description=_text(creation.get("description")),
            creator=_text((creation.get("creator") or {}).get("nick"))
            if isinstance(creation.get("creator"), dict)
            else None,
            license_name=None,
            tags=tuple(
                name
                for tag in tags_value
                if isinstance(tag, dict) and (name := _text(tag.get("name")))
            ),
            identity=ProviderIdentity(
                provider_id=_identifier(creation.get("id")) or slug,
                canonical_slug=canonical_slug,
                canonical_url=canonical_url,
            ),
        )


def _raise_for_status(response: httpx.Response) -> None:
    if 200 <= response.status_code < 300:
        return
    if response.status_code in {401, 403}:
        raise ProviderConnectionError("provider_auth_failed")
    if response.status_code == 404:
        raise ProviderConnectionError("provider_not_found")
    raise ProviderConnectionError(
        "provider_request_failed", retryable=response.status_code >= 500
    )


def _json_object(response: httpx.Response) -> dict[str, object]:
    if len(response.content) > _MAX_RESPONSE_BYTES:
        raise ProviderConnectionError("provider_response_invalid")
    try:
        payload = response.json()
    except ValueError:
        raise ProviderConnectionError("provider_response_invalid") from None
    if not isinstance(payload, dict):
        raise ProviderConnectionError("provider_response_invalid")
    _validate_json_value(payload)
    return payload


def _validate_json_value(value: object, *, depth: int = 0) -> None:
    if depth > _MAX_JSON_DEPTH:
        raise ProviderConnectionError("provider_response_invalid")
    if isinstance(value, str):
        if len(value) > _MAX_JSON_STRING_LENGTH:
            raise ProviderConnectionError("provider_response_invalid")
        return
    if value is None or isinstance(value, bool | int | float):
        return
    if isinstance(value, dict):
        if len(value) > _MAX_JSON_FIELDS:
            raise ProviderConnectionError("provider_response_invalid")
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > _MAX_JSON_STRING_LENGTH:
                raise ProviderConnectionError("provider_response_invalid")
            _validate_json_value(child, depth=depth + 1)
        return
    if isinstance(value, list):
        if len(value) > _MAX_JSON_LIST_ITEMS:
            raise ProviderConnectionError("provider_response_invalid")
        for child in value:
            _validate_json_value(child, depth=depth + 1)
        return
    raise ProviderConnectionError("provider_response_invalid")


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _identifier(value: object) -> str | None:
    if isinstance(value, int):
        return str(value)
    return _text(value)


def _require_identifier(value: str) -> None:
    if not _REMOTE_IDENTIFIER.fullmatch(value):
        raise ProviderConnectionError("provider_identifier_invalid")


def _require_public_https_url(value: object) -> None:
    url = _text(value)
    if url is None:
        raise ProviderConnectionError("provider_response_invalid")
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ProviderConnectionError("provider_response_invalid") from None
    if (
        parsed.scheme != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise ProviderConnectionError("provider_response_invalid")
    try:
        if not ip_address(hostname).is_global:
            raise ProviderConnectionError("provider_response_invalid")
    except ValueError:
        if hostname.lower() == "localhost" or hostname.lower().endswith(".local"):
            raise ProviderConnectionError("provider_response_invalid") from None


def _cults_identity_from_url(value: object) -> tuple[str, str]:
    """Return a normalized Cults URL and its terminal model slug."""
    url = _text(value)
    if url is None:
        raise ProviderConnectionError("provider_response_invalid")
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise ProviderConnectionError("provider_response_invalid") from None
    # Cults canonical evidence is an opaque identity, never a bearer URL.  Do
    # this check before deriving path parts or rebuilding the URL so query,
    # fragment, and userinfo values cannot be silently normalized away.
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "?" in url
        or "#" in url
    ):
        raise ProviderConnectionError("provider_response_invalid")
    hostname = (parsed.hostname or "").lower().removeprefix("www.")
    if hostname != _CULTS_CANONICAL_HOST or parsed.port not in {None, 443}:
        raise ProviderConnectionError("provider_response_invalid")
    parts = [part for part in parsed.path.split("/") if part]
    slug = parts[-1] if parts else ""
    if not slug or not _REMOTE_IDENTIFIER.fullmatch(slug):
        raise ProviderConnectionError("provider_response_invalid")
    normalized = urlunsplit(
        ("https", _CULTS_CANONICAL_HOST, "/" + "/".join(parts), "", "")
    )
    return normalized, slug


def _model_from_payload(
    payload: dict[str, object], fallback_id: str
) -> ProviderModelMetadata:
    files_value = payload.get("files")
    files: list[ProviderFileMetadata] = []
    if isinstance(files_value, list):
        if len(files_value) > _MAX_PROVIDER_FILES:
            raise ProviderConnectionError("provider_response_invalid")
        for file in files_value:
            if not isinstance(file, dict):
                continue
            file_id = _identifier(file.get("id"))
            name = _text(file.get("name"))
            if not file_id or not name:
                continue
            files.append(
                ProviderFileMetadata(
                    file_id=file_id,
                    name=name,
                    size_bytes=(
                        file.get("size")
                        if isinstance(file.get("size"), int)
                        else int(file["size"])
                        if isinstance(file.get("size"), str) and file["size"].isdigit()
                        else None
                    ),
                )
            )
    creator = payload.get("creator")
    provider_id = _identifier(payload.get("id")) or fallback_id
    # MMF's object response must carry page identity independently from its
    # API object ID. The resolver rejects metadata that lacks this evidence;
    # transient download links are intentionally not considered canonical.
    canonical_url = next(
        (
            value
            for key in ("canonical_url", "url", "page_url")
            if isinstance(value := payload.get(key), str) and value.strip()
        ),
        None,
    )
    return ProviderModelMetadata(
        model_id=provider_id,
        title=_text(payload.get("name")) or fallback_id,
        description=_text(payload.get("description")),
        creator=_text(creator.get("name")) if isinstance(creator, dict) else None,
        license_name=_text(payload.get("license")),
        files=tuple(files),
        identity=ProviderIdentity(
            provider_id=provider_id,
            canonical_url=canonical_url,
        ),
    )
