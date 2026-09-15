"""Transport rejects forbidden operations and bounds retries independently of provider payloads."""

from unittest.mock import patch

import httpx
import pytest
from printstash_core.inference import EmbeddingError
from printstash_core.inference.context import InferenceContext

from app.modules.inference import transport
from app.modules.inference.endpoint import EndpointConfig


@pytest.fixture(autouse=True)
def reset_transport():
    transport.close_client()
    yield
    transport.close_client()


@pytest.fixture
def endpoint():
    return EndpointConfig(base_url="http://test/v1", model="test")


class TestPostJson:
    def test_rejects_numeric_overflow(self, endpoint):
        with patch(
            "app.modules.inference.http_runtime.request",
            return_value=(200, {}, b'{"score":1e999}'),
        ):
            with pytest.raises(EmbeddingError, match="inference_invalid_json"):
                transport.post_json(endpoint, "embeddings", {})

    def test_rejects_unregistered_operations(self, endpoint):
        with pytest.raises(EmbeddingError, match="inference_operation_invalid"):
            transport.post_json(endpoint, "../admin", {})

    def test_bounds_outbound_bytes(self, endpoint):
        with pytest.raises(EmbeddingError, match="inference_request_too_large"):
            transport.post_json(
                endpoint, "embeddings", {"input": "x" * transport.MAX_REQUEST_BYTES}
            )

    @pytest.mark.parametrize(
        ("error", "code"),
        [
            (httpx.ConnectError("test-credential"), "inference_network_unavailable"),
            (ValueError("test-secret"), "inference_response_invalid"),
        ],
        ids=["connection", "unexpected-response"],
    )
    def test_sanitizes_transport_failures(self, endpoint, error, code):
        with patch("app.modules.inference.http_runtime.request", side_effect=error):
            with pytest.raises(EmbeddingError, match=code):
                transport.post_json(endpoint, "embeddings", {})

    def test_rejects_non_object_json(self, endpoint):
        with patch(
            "app.modules.inference.http_runtime.request", return_value=(200, {}, b"[]")
        ):
            with pytest.raises(EmbeddingError, match="inference_invalid_json"):
                transport.post_json(endpoint, "embeddings", {})

    def test_sanitizes_non_json_rejections(self, endpoint):
        with patch(
            "app.modules.inference.http_runtime.request",
            return_value=(400, {}, b"test-secret"),
        ):
            with pytest.raises(
                transport.EndpointError, match="inference_request_rejected"
            ) as caught:
                transport.post_json(endpoint, "embeddings", {})

        assert caught.value.unsupported is False

    def test_requires_structured_unsupported_errors(self, endpoint):
        with patch(
            "app.modules.inference.http_runtime.request",
            return_value=(400, {}, b'{"error":"unsupported response_format"}'),
        ):
            with pytest.raises(transport.EndpointError) as caught:
                transport.post_json(endpoint, "embeddings", {})

        assert caught.value.unsupported is False

    def test_cancels_before_admission(self, endpoint):
        with pytest.raises(EmbeddingError, match="inference_cancelled"):
            transport.post_json(
                endpoint,
                "embeddings",
                {},
                context=InferenceContext.bounded(cancelled=lambda: True),
            )


class TestRetryDelay:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("-1", 0),
            ("3600", 2),
            ("1", 1),
            ("Thu, 01 Jan 1970 00:00:00 GMT", 0),
            ("Thu, 01 Jan 2099 00:00:00 GMT", 2),
        ],
        ids=["negative", "cap", "seconds", "past-date", "future-date"],
    )
    def test_bounds_server_retry_delays(self, value, expected):
        assert transport._retry_delay(value, 0) == expected

    @pytest.mark.parametrize(
        "value", [None, "invalid date"], ids=["absent", "malformed"]
    )
    def test_uses_bounded_jitter_for_invalid_delays(self, value):
        assert 0.1 <= transport._retry_delay(value, 0) <= 0.15
