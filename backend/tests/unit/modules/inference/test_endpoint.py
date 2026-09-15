"""Endpoint configuration permits administrator LAN hosts while rejecting URL/header ambiguity."""

import pytest
from pydantic import ValidationError

from app.modules.inference.endpoint import EndpointConfig


class TestEndpointConfig:
    def test_preserves_legacy_endpoint_identity(self):
        endpoint = EndpointConfig(
            base_url="http://inference.test/v1",
            model="fixture",
            configuration_version="1" * 32,
        )
        assert (
            endpoint.identity
            == "4809db6aca1e27b2686c2695186986f3ee1a0a837eda16ef22174b33a8ec2f84"
        )

    @pytest.mark.parametrize(
        "repository",
        ["model", "../model", "org/model/extra", "org/model?token=value", "org/モデル"],
    )
    def test_rejects_ambiguous_model_repositories(self, repository):
        with pytest.raises(ValidationError):
            EndpointConfig(
                base_url="http://inference.test/v1",
                model="fixture",
                model_repo=repository,
            )

    @pytest.mark.parametrize(
        ("url", "host"),
        [
            ("http://192.168.1.10:11434/v1/", "192.168.1.10"),
            ("https://EXAMPLE.test/v1", "example.test"),
            ("http://[::1]:8000/v1", "::1"),
        ],
        ids=["lan", "https", "ipv6"],
    )
    def test_accepts_explicit_admin_endpoints(self, url, host):
        endpoint = EndpointConfig(base_url=url, model="test")

        assert endpoint.host == host
        assert not endpoint.base_url.endswith("/")

    @pytest.mark.parametrize(
        "url",
        [
            "file:///tmp/provider",
            "https://user:secret@test/v1",
            "https://test/v1?token=secret",
            "https://test/v1#part",
            "https://test:70000/v1",
            "https://test/a/../v1",
            "https://test/a/%2e%2e/v1",
            "https://test/\\other",
            "https://test/\nother",
            "https:///v1",
        ],
        ids=[
            "scheme",
            "credentials",
            "query",
            "fragment",
            "port",
            "traversal",
            "encoded-traversal",
            "backslash",
            "control",
            "missing-host",
        ],
    )
    def test_rejects_ambiguous_endpoints(self, url):
        with pytest.raises(ValidationError):
            EndpointConfig(base_url=url, model="test")

    @pytest.mark.parametrize(
        "headers",
        [
            {"Host": "other"},
            {"X-Key": "one", "x-key": "two"},
            {"X-Key": "secret\nnewline"},
            {"Content-Length": "1"},
            {"invalid name": "secret"},
            {"X-Key": "s" * 8193},
        ],
        ids=["host", "duplicate", "control", "framing", "name", "size"],
    )
    def test_rejects_ambiguous_headers(self, headers):
        with pytest.raises(ValidationError, match="inference_header_invalid"):
            EndpointConfig(base_url="http://test/v1", model="test", headers=headers)

    def test_rejects_double_authorization(self):
        with pytest.raises(ValidationError, match="inference_authorization_ambiguous"):
            EndpointConfig(
                base_url="http://test/v1",
                model="test",
                api_key="test-key",
                headers={"Authorization": "Bearer test-header"},
            )

    def test_rejects_key_header_injection(self):
        with pytest.raises(ValidationError, match="inference_header_invalid"):
            EndpointConfig(
                base_url="http://test/v1",
                model="test",
                api_key="test-key\r\nX-Key: bad",
            )

    def test_preserves_secret_headers_at_the_wire_boundary(self):
        endpoint = EndpointConfig(
            base_url="http://test/v1",
            model="test",
            api_key="test-key",
            headers={"X-Test": "test-header"},
        )

        assert endpoint.request_headers() == {
            "Authorization": "Bearer test-key",
            "X-Test": "test-header",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
        }
        assert "test-key" not in repr(endpoint)
        assert "test-header" not in repr(endpoint)
