"""Two provider APIs, their credentials, and their deliberately different identities.

MyMiniFactory and Cults are the credentialed providers, and this file covers the
narrow adapters that talk to them. Three themes, each a promise to the user.

**Credentials are exchanged, never retained.** The OAuth flow trades a code for
tokens and refreshes them; what it must not do is keep the user's credentials
anywhere. A retained secret is a secret that can leak later.

**Identity is two things, not one.** Cults returns an opaque creation id *and* a
URL slug, and they are genuinely different values — comparing the opaque id
against a slug is how a capture gets attributed to the wrong model. So the slug
binds the page and the opaque id binds the API object, and a creation URL for a
different slug is refused outright.

**Their responses are untrusted input.** Everything crossing this boundary is
bounded before it reaches a database, a UI or a log: body size, string length,
object width, list length, key length and nesting depth. Each has a row, and each
refuses with the same opaque code so the provider's own content never becomes the
error message.

Cults notably returns metadata *only* — the browser supplies the bytes — so the
"never requests files" row is a contract, not an omission.
"""

from __future__ import annotations

import httpx
import pytest

from app.services import capture_provider_connections as connections_module
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


class TestMyMiniFactoryMetadataClient:
    @pytest.mark.anyio
    async def test_the_mmf_token_flow_never_retains_the_credentials(
        self,
    ) -> None:
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
        credentials = MyMiniFactoryCredentials(
            client_id="client", client_secret="secret"
        )

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
    async def test_mmf_parses_metadata_fixture_into_a_file_safe_contract(self) -> None:
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
    async def test_mmf_rejects_oversized_metadata_without_echoing_it(self) -> None:
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
    async def test_mmf_rejects_malformed_json_without_exposing_response_content(
        self,
    ) -> None:
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


class TestCultsMetadataClient:
    @pytest.mark.anyio
    async def test_the_cults_metadata_query_asks_for_no_download_fields(
        self,
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
    @pytest.mark.parametrize("source_url", ["http://127.0.0.1/admin", "not-a-url"])
    async def test_cults_rejects_private_or_malformed_provider_urls(
        self,
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
        self, source_url: str, secret: str
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


class TestCultsIdentityFromUrl:
    @pytest.mark.anyio
    async def test_cults_identity_separates_url_slug_from_opaque_creation_id(
        self,
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
    async def test_cults_identity_accepts_numeric_opaque_creation_id(self) -> None:
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
    async def test_cults_rejects_creation_url_for_a_different_slug(self) -> None:
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
    async def test_cults_rejects_opaque_id_matching_slug_without_canonical_url(
        self,
    ) -> None:
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


class TestProviderConnectionError:
    @pytest.mark.anyio
    async def test_a_provider_auth_failure_raises_a_typed_error(self) -> None:
        transport = RecordingTransport(
            [httpx.Response(401, json={"error": "secret response"})]
        )
        client = MyMiniFactoryMetadataClient(transport)

        with pytest.raises(ProviderConnectionError) as exc:
            await client.model_metadata(
                "7", MyMiniFactoryTokens("access", "refresh", 60)
            )

        assert exc.value.code == "provider_auth_failed"


class TestSecretRepr:
    """No credential-carrying value renders its secret.

    Every one of these objects is repr'd somewhere a human or a log file can
    read it — a settings dump, an exception chain, a crash report. Redaction is
    a property of the type rather than of each call site, because a call site
    that forgets is not visible until the secret is already in a log."""

    def test_no_repr_in_this_module_renders_a_secret(self) -> None:
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


class TestJsonObject:
    """Every provider response is bounded before it is read as data.

    A provider's JSON is untrusted input that reaches a database, a UI, and a log. The
    guards here are all about the *shape* rather than the meaning: a body larger than the
    cap, a string longer than a field can hold, an object with more keys than any real
    payload has, a list longer than any real one, and nesting deep enough to blow the
    parser's stack. Each is refused with the same opaque code so the response's own
    content never becomes the error message.
    """

    def _response(self, payload: object) -> httpx.Response:
        import json

        return httpx.Response(200, content=json.dumps(payload).encode("utf-8"))

    def test_reads_an_ordinary_object(self) -> None:
        assert connections_module._json_object(self._response({"a": 1})) == {"a": 1}

    def test_refuses_a_body_larger_than_the_cap(self) -> None:
        oversized = httpx.Response(
            200, content=b"{}" + b" " * connections_module._MAX_RESPONSE_BYTES
        )

        with pytest.raises(ProviderConnectionError, match="provider_response_invalid"):
            connections_module._json_object(oversized)

    def test_refuses_something_that_is_not_json(self) -> None:
        with pytest.raises(ProviderConnectionError, match="provider_response_invalid"):
            connections_module._json_object(httpx.Response(200, content=b"{not json"))

    def test_refuses_json_that_is_not_an_object(self) -> None:
        with pytest.raises(ProviderConnectionError, match="provider_response_invalid"):
            connections_module._json_object(self._response([1, 2, 3]))

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("text", id="string"),
            pytest.param(1, id="int"),
            pytest.param(1.5, id="float"),
            pytest.param(True, id="bool"),
            pytest.param(None, id="null"),
            pytest.param({"a": [1, {"b": "c"}]}, id="nested-mix"),
        ],
    )
    def test_accepts_every_json_value_it_should(self, value: object) -> None:
        connections_module._validate_json_value(value)

    def test_refuses_a_string_longer_than_a_field_can_hold(self) -> None:
        oversized = "x" * (connections_module._MAX_JSON_STRING_LENGTH + 1)

        with pytest.raises(ProviderConnectionError, match="provider_response_invalid"):
            connections_module._validate_json_value(oversized)

    def test_refuses_an_object_with_more_keys_than_any_real_payload(self) -> None:
        wide = {
            str(index): index
            for index in range(connections_module._MAX_JSON_FIELDS + 1)
        }

        with pytest.raises(ProviderConnectionError, match="provider_response_invalid"):
            connections_module._validate_json_value(wide)

    def test_refuses_a_key_that_is_too_long(self) -> None:
        long_key = "k" * (connections_module._MAX_JSON_STRING_LENGTH + 1)

        with pytest.raises(ProviderConnectionError, match="provider_response_invalid"):
            connections_module._validate_json_value({long_key: 1})

    def test_refuses_a_list_longer_than_any_real_one(self) -> None:
        long_list = list(range(connections_module._MAX_JSON_LIST_ITEMS + 1))

        with pytest.raises(ProviderConnectionError, match="provider_response_invalid"):
            connections_module._validate_json_value(long_list)

    def test_refuses_nesting_deep_enough_to_exhaust_the_stack(self) -> None:
        deep: object = 1
        for _ in range(connections_module._MAX_JSON_DEPTH + 2):
            deep = {"a": deep}

        with pytest.raises(ProviderConnectionError, match="provider_response_invalid"):
            connections_module._validate_json_value(deep)

    def test_refuses_a_value_of_a_type_json_cannot_carry(self) -> None:
        # Only reachable from a hand-built payload, but the guard is a closed
        # allowlist rather than a blocklist, which is why it is here.
        with pytest.raises(ProviderConnectionError, match="provider_response_invalid"):
            connections_module._validate_json_value(object())


class TestRaiseForStatus:
    """Each upstream status maps to a code the caller can act on differently."""

    def _raise(self, status: int) -> None:
        connections_module._raise_for_status(httpx.Response(status))

    @pytest.mark.parametrize("status", [200, 201, 204, 299])
    def test_lets_a_success_through(self, status: int) -> None:
        self._raise(status)

    @pytest.mark.parametrize("status", [401, 403], ids=["unauthorized", "forbidden"])
    def test_maps_an_auth_failure(self, status: int) -> None:
        # The caller refreshes the token exactly once on this code, so it cannot
        # be folded into the generic failure.
        with pytest.raises(ProviderConnectionError, match="provider_auth_failed"):
            self._raise(status)

    def test_maps_a_missing_page(self) -> None:
        with pytest.raises(ProviderConnectionError, match="provider_not_found"):
            self._raise(404)

    def test_marks_a_server_error_retryable(self) -> None:
        with pytest.raises(ProviderConnectionError) as exc_info:
            self._raise(503)

        assert exc_info.value.retryable is True

    def test_does_not_mark_a_client_error_retryable(self) -> None:
        # Retrying a 400 just sends the same bad request again.
        with pytest.raises(ProviderConnectionError) as exc_info:
            self._raise(400)

        assert exc_info.value.retryable is False


class TestText:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            pytest.param("  Widget  ", "Widget", id="trimmed"),
            pytest.param("Widget", "Widget", id="plain"),
        ],
    )
    def test_returns_a_trimmed_string(self, value: str, expected: str) -> None:
        assert connections_module._text(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("", id="empty"),
            pytest.param("   ", id="whitespace"),
            pytest.param(1, id="not-a-string"),
            pytest.param(None, id="absent"),
        ],
    )
    def test_treats_anything_else_as_absent(self, value: object) -> None:
        # A provider sending `""` for a title means "no title", not a model
        # named nothing.
        assert connections_module._text(value) is None
