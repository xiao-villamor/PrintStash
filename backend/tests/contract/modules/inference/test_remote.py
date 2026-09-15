"""Remote embeddings honor the native vector and bounded HTTP contracts over sockets."""

import threading
import time

import pytest
from printstash_core.inference import EmbeddingError, EmbeddingInput, EmbeddingSpace
from printstash_core.inference.context import InferenceContext
from pydantic import SecretStr

from app.modules.inference.endpoint import EndpointConfig
from app.modules.inference.remote import RemoteEmbeddingProvider
from app.modules.inference.transport import close_client, post_json
from tests.fakes.inference import InferenceFake
from tests.fakes.server import start_server


@pytest.fixture
def remote():
    fake = InferenceFake()
    running = start_server(fake.app())
    endpoint = EndpointConfig(
        base_url=running.base_url + "/v1",
        model=fake.model,
        api_key=SecretStr("test-api-key"),
    )
    space = EmbeddingSpace(
        model_key=fake.model,
        model_revision=endpoint.revision,
        dimension=4,
        modality="text",
        render_recipe="passages-v1",
        provider="openai_compatible",
        profile="semantic_text",
        provider_config_hash=endpoint.identity,
    )
    provider = RemoteEmbeddingProvider(endpoint, space)
    try:
        yield fake, provider
    finally:
        close_client()
        running.stop()


class TestRemoteEmbeddingProvider:
    @pytest.mark.parametrize("modality", ["image", "point_cloud"])
    def test_never_sends_undeclared_visual_modalities(self, remote, modality):
        fake, provider = remote
        provider.embed((EmbeddingInput("text", text="boat"),), provider.space)
        visual = (
            EmbeddingInput("image", rgb=b"RGB", width=1, height=1)
            if modality == "image"
            else EmbeddingInput("point_cloud", points=b"\x00" * (6 * 10_000 * 4))
        )

        with pytest.raises(EmbeddingError, match="embedding_image_unavailable"):
            provider.embed((visual,), provider.space)

        assert len(fake.calls) == 1
        assert fake.calls[0]["body"]["input"] == ["boat"]

    @pytest.mark.parametrize("fault", ["401", "500"])
    def test_redacts_failed_request_secrets(
        self, remote, caplog, fault
    ):
        fake, provider = remote
        fake.fault = fault
        caplog.set_level("DEBUG")

        with pytest.raises(EmbeddingError) as failure:
            provider.embed(
                (EmbeddingInput("text", text="private-failed-query"),), provider.space
            )

        assert fake.calls
        assert fake.calls[0]["headers"]["authorization"] == "Bearer test-api-key"
        rendered = caplog.text + str(failure.value)
        for private in (
            "test-api-key",
            "test-secret-must-not-leak",
            "private-failed-query",
            provider.endpoint.base_url,
        ):
            assert private not in rendered

    def test_keeps_remote_inference_available_without_onnx(self, remote, monkeypatch):
        import sys

        fake, provider = remote
        for name in ("onnx", "onnxruntime", "tokenizers"):
            monkeypatch.setitem(sys.modules, name, None)
        vector = provider.embed((EmbeddingInput("text", text="boat"),), provider.space)[
            0
        ]
        assert vector == pytest.approx((0.70710678, 0.70710678, 0, 0))
        assert len(fake.calls) == 1

    def test_preserves_endpoint_deadline_inside_a_longer_job(self, remote):
        fake, provider = remote
        fake.fault = "trickle"
        endpoint = provider.endpoint.model_copy(update={"timeout_seconds": 0.3})
        started = time.monotonic()

        with pytest.raises(EmbeddingError, match="inference_timeout"):
            post_json(
                endpoint,
                "embeddings",
                {"model": fake.model, "input": ["boat"], "encoding_format": "float"},
                context=InferenceContext.bounded(120),
            )

        assert time.monotonic() - started < 1
        assert len(fake.calls) == 1

    def test_keeps_inference_wire_data_out_of_debug_logs(self, remote, caplog):
        fake, provider = remote
        caplog.set_level("DEBUG")

        provider.embed(
            (EmbeddingInput("text", text="test-private-search-query"),), provider.space
        )

        assert len(fake.calls) == 1
        assert "test-private-search-query" not in caplog.text
        assert "test-api-key" not in caplog.text
        assert "test-upstream-secret" not in caplog.text
        assert provider.endpoint.base_url not in caplog.text

    def test_embeds_ordered_text_batches(self, remote):
        fake, provider = remote
        inputs = (
            EmbeddingInput("text", text="boat"),
            EmbeddingInput("text", text="gear"),
        )

        result = provider.embed(inputs, provider.space)

        assert result[0] == pytest.approx((0.70710678, 0.70710678, 0, 0))
        assert result[1] == pytest.approx((0.89442719, 0.44721359, 0, 0))
        assert fake.calls[0]["body"] == {
            "model": fake.model,
            "input": ["boat", "gear"],
            "encoding_format": "float",
        }

    @pytest.mark.parametrize(
        ("fault", "code"),
        [
            ("missing", "embedding_response_invalid"),
            ("duplicate", "embedding_response_invalid"),
            ("dimension", "embedding_dimension_mismatch"),
            ("zero", "embedding_vector_invalid"),
            ("nonfinite", "inference_invalid_json"),
            ("boolean", "embedding_dimension_mismatch"),
            ("index", "embedding_response_invalid"),
            ("json", "inference_invalid_json"),
        ],
        ids=lambda value: value,
    )
    def test_rejects_invalid_embedding_outputs(self, remote, fault, code):
        fake, provider = remote
        fake.fault = fault

        with pytest.raises(EmbeddingError, match=code):
            provider.embed(
                (
                    EmbeddingInput("text", text="boat"),
                    EmbeddingInput("text", text="gear"),
                ),
                provider.space,
            )

        assert len(fake.calls) == 1

    @pytest.mark.parametrize(
        ("fault", "code"),
        [
            ("oversized", "inference_response_too_large"),
            ("compressed", "inference_response_encoding"),
        ],
        ids=["bytes", "compressed"],
    )
    def test_bounds_remote_response_bytes(self, remote, fault, code):
        fake, provider = remote
        fake.fault = fault

        with pytest.raises(EmbeddingError, match=code):
            provider.validate()

        assert len(fake.calls) == 1

    def test_retries_bounded_rate_limits(self, remote):
        fake, provider = remote
        fake.failures_remaining = 1

        provider.validate()

        assert len(fake.calls) == 2

    def test_refuses_redirected_embeddings(self, remote):
        fake, provider = remote
        fake.fault = "redirect"

        with pytest.raises(EmbeddingError, match="inference_redirect_refused"):
            provider.validate()

        assert fake.redirected == 0

    def test_opens_a_failed_endpoint_circuit(self, remote):
        fake, provider = remote
        fake.fault = "500"
        with pytest.raises(EmbeddingError, match="inference_endpoint_failed"):
            provider.validate()
        with pytest.raises(EmbeddingError, match="inference_endpoint_failed"):
            provider.validate()
        with pytest.raises(EmbeddingError, match="inference_endpoint_failed"):
            provider.validate()

        with pytest.raises(EmbeddingError, match="inference_circuit_open"):
            provider.validate()

        assert len(fake.calls) == 9

    @pytest.mark.parametrize(
        "fault", ["timeout", "trickle"], ids=["stalled", "trickling"]
    )
    def test_bounds_the_whole_operation(self, remote, fault):
        fake, provider = remote
        fake.fault = fault
        started = time.monotonic()

        with pytest.raises(EmbeddingError, match="inference_timeout"):
            provider.embed(
                (EmbeddingInput("text", text="boat"),),
                provider.space,
                context=InferenceContext.bounded(0.3),
            )

        assert time.monotonic() - started < 1
        assert len(fake.calls) == 1

    def test_cancels_remote_inference(self, remote):
        fake, provider = remote
        fake.fault = "trickle"
        cancel = threading.Event()
        timer = threading.Timer(0.2, cancel.set)
        timer.start()

        with pytest.raises(EmbeddingError, match="inference_cancelled"):
            provider.embed(
                (EmbeddingInput("text", text="boat"),),
                provider.space,
                context=InferenceContext.bounded(cancelled=cancel.is_set),
            )

        timer.join()
        assert len(fake.calls) == 1

    def test_does_not_retry_rejected_credentials(self, remote):
        fake, provider = remote
        fake.fault = "401"

        with pytest.raises(EmbeddingError, match="^inference_request_rejected$"):
            provider.validate()

        assert len(fake.calls) == 1
