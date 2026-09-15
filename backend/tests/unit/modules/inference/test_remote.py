"""Invalid embedding requests fail before any remote work or partial result."""

from dataclasses import replace

import pytest
from printstash_core.inference import EmbeddingError, EmbeddingInput, EmbeddingSpace

from app.modules.inference.endpoint import EndpointConfig
from app.modules.inference.remote import RemoteEmbeddingProvider


@pytest.fixture
def remote():
    endpoint = EndpointConfig(base_url="http://test/v1", model="test")
    space = EmbeddingSpace(
        model_key="test",
        model_revision=endpoint.revision,
        dimension=4,
        modality="text",
        render_recipe="v1",
        provider="openai_compatible",
        provider_config_hash=endpoint.identity,
    )
    return RemoteEmbeddingProvider(endpoint, space)


class TestRemoteEmbeddingProvider:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("provider", "onnx_cpu"),
            ("model_key", "another"),
            ("model_revision", "changed"),
            ("modality", "image"),
            ("provider_config_hash", "a" * 64),
        ],
        ids=["provider", "model", "revision", "modality", "version"],
    )
    def test_rejects_incompatible_endpoint_spaces(self, remote, field, value):
        with pytest.raises(EmbeddingError, match="embedding_space_mismatch"):
            RemoteEmbeddingProvider(
                remote.endpoint, replace(remote.space, **{field: value})
            )

    @pytest.mark.parametrize(
        "change", [{"dimension": 8}, {"model_key": "another"}, {"model_revision": "v2"}]
    )
    def test_rejects_changed_request_spaces(self, remote, change):
        with pytest.raises(EmbeddingError, match="embedding_space_mismatch"):
            remote.embed(
                (EmbeddingInput("text", text="boat"),),
                replace(remote.space, **change),
            )

    @pytest.mark.parametrize("count", [0, 9], ids=["empty", "over-cap"])
    def test_rejects_invalid_batch_sizes(self, remote, count):
        with pytest.raises(EmbeddingError, match="embedding_batch_budget"):
            remote.embed((EmbeddingInput("text", text="boat"),) * count, remote.space)

    def test_rejects_images_without_a_wire_contract(self, remote):
        with pytest.raises(EmbeddingError, match="embedding_image_unavailable"):
            remote.embed(
                (EmbeddingInput("image", rgb=b"\x00\x00\x00", width=1, height=1),),
                remote.space,
            )

    def test_rejects_inputs_above_the_endpoint_budget(self, remote):
        endpoint = remote.endpoint.model_copy(update={"max_input_characters": 128})
        provider = RemoteEmbeddingProvider(
            endpoint, replace(remote.space, provider_config_hash=endpoint.identity)
        )

        with pytest.raises(EmbeddingError, match="embedding_input_limit_exceeded"):
            provider.embed((EmbeddingInput("text", text="a" * 129),), provider.space)
