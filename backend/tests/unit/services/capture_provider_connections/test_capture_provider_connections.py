"""Defends ``test_mmf_exchanges_and_refreshes_tokens_without_retaining_credentials`` behavior for the ``capture_provider_connections`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import httpx
import pytest

from app.core.config import FrozenSettings
from app.services.capture_provider_connections import (
    CultsCredentials,
    CultsMetadataClient,
    MyMiniFactoryCredentials,
    MyMiniFactoryMetadataClient,
    MyMiniFactoryTokens,
    ProviderConnectionError,
    ProviderIdentity,
)


class RecordingTransport:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    async def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        self.calls.append({"method": method, "url": url, **kwargs})
        response = self.responses.pop(0)
        response.request = httpx.Request(method, url)
        return response


@pytest.mark.anyio
async def test_mmf_exchanges_and_refreshes_tokens_without_retaining_credentials() -> (
    None
):
    transport = RecordingTransport(
        [
            httpx.Response(
                200,
                json={
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "expires_in": 3600,
                },
            ),
            httpx.Response(200, json={"access_token": "new", "expires_in": 1800}),
        ]
    )
    client = MyMiniFactoryMetadataClient(transport)
    credentials = MyMiniFactoryCredentials(client_id="client", client_secret="secret")

    exchanged = await client.exchange_code(
        credentials, code="code", redirect_uri="https://vault.example/callback"
    )
    refreshed = await client.refresh_tokens(credentials, exchanged)

    assert exchanged == MyMiniFactoryTokens("access", "refresh", 3600)
    assert refreshed == MyMiniFactoryTokens("new", "refresh", 1800)
    assert transport.calls[0]["data"] == {
        "grant_type": "authorization_code",
        "code": "code",
        "redirect_uri": "https://vault.example/callback",
        "client_id": "client",
        "client_secret": "secret",
    }
    assert transport.calls[1]["data"] == {
        "grant_type": "refresh_token",
        "refresh_token": "refresh",
        "client_id": "client",
        "client_secret": "secret",
    }
    assert not hasattr(client, "credentials")


@pytest.mark.anyio
async def test_mmf_parses_metadata_fixture_into_a_file_safe_contract() -> None:
    transport = RecordingTransport(
        [
            httpx.Response(
                200,
                json={
                    "id": 7,
                    "name": "Fixture model",
                    "description": "Plain text source",
                    "creator": {"name": "Maker"},
                    "license": "CC-BY",
                    "files": [{"id": 9, "name": "part.stl", "size": 42}],
                },
            )
        ]
    )
    client = MyMiniFactoryMetadataClient(transport)

    model = await client.model_metadata(
        "7", MyMiniFactoryTokens("access", "refresh", 60)
    )

    assert model.model_id == "7"
    assert model.title == "Fixture model"
    assert model.creator == "Maker"
    assert model.files[0].file_id == "9"
    assert model.files[0].name == "part.stl"
    assert "download" not in repr(model).lower()


@pytest.mark.anyio
async def test_cults_metadata_uses_basic_auth_and_never_requests_files() -> None:
    transport = RecordingTransport(
        [
            httpx.Response(
                200,
                json={
                    "data": {
                        "creation": {
                            "id": "design-1",
                            "name": "Fixture design",
                            "description": "Metadata only",
                            "url": "https://cults3d.com/en/3d-model/art/fixture",
                            "creator": {"nick": "maker"},
                            "tags": [{"name": "useful"}],
                        }
                    }
                },
            )
        ]
    )
    client = CultsMetadataClient(transport)

    model = await client.creation_metadata(
        "fixture", CultsCredentials("user", "password")
    )

    assert model.model_id == "design-1"
    assert model.tags == ("useful",)
    assert transport.calls[0]["auth"] == ("user", "password")
    query = transport.calls[0]["json"]["query"]  # type: ignore[index]
    assert "download" not in query.lower()
    assert "file" not in query.lower()


@pytest.mark.anyio
async def test_cults_identity_separates_url_slug_from_opaque_creation_id() -> None:
    transport = RecordingTransport(
        [
            httpx.Response(
                200,
                json={
                    "data": {
                        "creation": {
                            "id": "design-1",
                            "name": "Fixture design",
                            "url": "https://cults3d.com/en/3d-model/art/fixture",
                        }
                    }
                },
            )
        ]
    )

    model = await CultsMetadataClient(transport).creation_metadata(
        "fixture", CultsCredentials("user", "password")
    )

    assert model.model_id == "design-1"
    assert model.identity == ProviderIdentity(
        provider_id="design-1",
        canonical_slug="fixture",
        canonical_url="https://cults3d.com/en/3d-model/art/fixture",
    )


@pytest.mark.anyio
async def test_cults_identity_accepts_numeric_opaque_creation_id() -> None:
    transport = RecordingTransport(
        [
            httpx.Response(
                200,
                json={
                    "data": {
                        "creation": {
                            "id": 42,
                            "name": "Fixture design",
                            "url": "https://cults3d.com/en/3d-model/art/fixture",
                        }
                    }
                },
            )
        ]
    )

    model = await CultsMetadataClient(transport).creation_metadata(
        "fixture", CultsCredentials("user", "password")
    )

    assert model.identity == ProviderIdentity(
        provider_id="42",
        canonical_slug="fixture",
        canonical_url="https://cults3d.com/en/3d-model/art/fixture",
    )


@pytest.mark.anyio
async def test_cults_rejects_creation_url_for_a_different_slug() -> None:
    transport = RecordingTransport(
        [
            httpx.Response(
                200,
                json={
                    "data": {
                        "creation": {
                            "id": "other-id",
                            "name": "Other design",
                            "url": "https://cults3d.com/en/3d-model/art/other",
                        }
                    }
                },
            )
        ]
    )

    with pytest.raises(ProviderConnectionError) as exc:
        await CultsMetadataClient(transport).creation_metadata(
            "fixture", CultsCredentials("user", "password")
        )

    assert exc.value.code == "provider_response_invalid"


@pytest.mark.anyio
async def test_cults_rejects_opaque_id_matching_slug_without_canonical_url() -> None:
    transport = RecordingTransport(
        [
            httpx.Response(
                200,
                json={"data": {"creation": {"id": "fixture", "name": "Fixture"}}},
            )
        ]
    )

    with pytest.raises(ProviderConnectionError) as exc:
        await CultsMetadataClient(transport).creation_metadata(
            "fixture", CultsCredentials("user", "password")
        )

    assert exc.value.code == "provider_response_invalid"


@pytest.mark.anyio
async def test_connection_errors_are_safe_and_typed() -> None:
    transport = RecordingTransport(
        [httpx.Response(401, json={"error": "secret response"})]
    )
    client = MyMiniFactoryMetadataClient(transport)

    with pytest.raises(ProviderConnectionError) as exc:
        await client.model_metadata("7", MyMiniFactoryTokens("access", "refresh", 60))

    assert exc.value.code == "provider_auth_failed"


def test_credentials_tokens_and_errors_never_render_secrets() -> None:
    credentials = MyMiniFactoryCredentials(
        client_id="client-id", client_secret="client-secret"
    )
    tokens = MyMiniFactoryTokens("access-token", "refresh-token", 60)
    cults = CultsCredentials("maker@example.test", "cults-password")
    error = ProviderConnectionError("provider_response_invalid")

    rendered = " ".join(map(repr, (credentials, tokens, cults, error))) + str(error)

    for secret in (
        "client-id",
        "client-secret",
        "access-token",
        "refresh-token",
        "maker@example.test",
        "cults-password",
    ):
        assert secret not in rendered


def test_mmf_deployment_settings_redact_both_credentials() -> None:
    configured = FrozenSettings(
        _env_file=None, mmf_client_id="client-id", mmf_client_secret="client-secret"
    )

    rendered = repr(configured)

    assert "client-id" not in rendered
    assert "client-secret" not in rendered


@pytest.mark.anyio
@pytest.mark.parametrize("source_url", ["http://127.0.0.1/admin", "not-a-url"])
async def test_cults_rejects_private_or_malformed_provider_urls(
    source_url: str,
) -> None:
    transport = RecordingTransport(
        [
            httpx.Response(
                200,
                json={
                    "data": {
                        "creation": {
                            "id": "design-1",
                            "name": "Fixture design",
                            "url": source_url,
                            "creator": {"nick": "maker"},
                            "tags": [],
                        }
                    }
                },
            )
        ]
    )

    with pytest.raises(ProviderConnectionError) as exc:
        await CultsMetadataClient(transport).creation_metadata(
            "fixture", CultsCredentials("user", "password")
        )

    assert exc.value.code == "provider_response_invalid"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("source_url", "secret"),
    [
        (
            "https://cults3d.com/en/3d-model/art/fixture?token=stolen-token",
            "stolen-token",
        ),
        (
            "https://cults3d.com/en/3d-model/art/fixture#fragment-secret",
            "fragment-secret",
        ),
        (
            "https://stolen-user:stolen-password@cults3d.com/en/3d-model/art/fixture",
            "stolen-password",
        ),
    ],
)
async def test_cults_rejects_canonical_urls_with_credentials_query_or_fragment(
    source_url: str, secret: str
) -> None:
    transport = RecordingTransport(
        [
            httpx.Response(
                200,
                json={
                    "data": {
                        "creation": {
                            "id": "design-1",
                            "name": "Fixture design",
                            "url": source_url,
                        }
                    }
                },
            )
        ]
    )

    with pytest.raises(ProviderConnectionError) as exc:
        await CultsMetadataClient(transport).creation_metadata(
            "fixture", CultsCredentials("user", "password")
        )

    assert exc.value.code == "provider_response_invalid"
    assert secret not in str(exc.value)
    assert secret not in repr(exc.value)


@pytest.mark.anyio
async def test_mmf_rejects_oversized_metadata_without_echoing_it() -> None:
    oversized_title = "x" * 16_385
    transport = RecordingTransport(
        [httpx.Response(200, json={"id": "7", "name": oversized_title})]
    )

    with pytest.raises(ProviderConnectionError) as exc:
        await MyMiniFactoryMetadataClient(transport).model_metadata(
            "7", MyMiniFactoryTokens("access", "refresh", 60)
        )

    assert exc.value.code == "provider_response_invalid"
    assert oversized_title not in str(exc.value)


@pytest.mark.anyio
async def test_mmf_rejects_malformed_json_without_exposing_response_content() -> None:
    response_body = b'{"access_token":"secret-token", malformed'
    transport = RecordingTransport([httpx.Response(200, content=response_body)])

    with pytest.raises(ProviderConnectionError) as exc:
        await MyMiniFactoryMetadataClient(transport).exchange_code(
            MyMiniFactoryCredentials("client", "client-secret"),
            code="code",
            redirect_uri="https://vault.example/callback",
        )

    assert exc.value.code == "provider_response_invalid"
    assert "secret-token" not in repr(exc.value)
    assert "secret-token" not in str(exc.value)
