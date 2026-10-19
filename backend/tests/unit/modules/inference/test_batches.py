"""Prepared view units retain their result association across provider batches."""

from unittest.mock import Mock, call

import pytest
from printstash_core.inference import (
    EmbeddingError,
    EmbeddingInput,
    EmbeddingProvider,
    EmbeddingSpace,
)

from app.modules.inference.batches import embed_units


class TestPreparedBatches:
    def test_retains_unit_association_across_tensor_batches(self):
        space = EmbeddingSpace("prepared-views", "v1", 12, "image", "test")
        views = tuple(
            EmbeddingInput("image", rgb=bytes((index, 0, 0)), width=1, height=1)
            for index in range(12)
        )
        # Distinct normalized provider outputs make misplaced views observable.
        vectors = tuple(
            tuple(float(component == index) for component in range(12))
            for index in range(12)
        )
        expected_by_view = dict(zip(views, vectors, strict=True))
        provider = Mock(spec=EmbeddingProvider)
        provider.embed.side_effect = lambda inputs, requested_space: tuple(
            expected_by_view[view] for view in inputs
        )

        result = embed_units(provider, (views[:6], views[6:]), space)

        assert result == (vectors[:6], vectors[6:])
        assert provider.embed.call_args_list == [
            call(views[:8], space),
            call(views[8:], space),
        ]


class TestBatchFailures:
    @pytest.mark.parametrize("units", [(), ((),), tuple((1,) for _ in range(9))])
    def test_refuses_invalid_unit_counts(self, units):
        with pytest.raises(EmbeddingError, match="embedding_batch_budget"):
            embed_units(Mock(), units, None)

    @pytest.mark.parametrize("views,side", [(17, 1), (5, 1024)], ids=["items", "bytes"])
    def test_refuses_oversized_input(self, views, side):
        item = EmbeddingInput(
            "image", rgb=b"x" * (side * side * 3), width=side, height=side
        )
        with pytest.raises(EmbeddingError, match="embedding_input_budget"):
            embed_units(Mock(), ((item,) * views,), None)

    def test_recovers_healthy_units_after_a_member_fails(self):
        good = EmbeddingInput("text", text="good")
        bad = EmbeddingInput("text", text="bad")
        provider = Mock()

        def embed(inputs, space, **kwargs):
            if bad in inputs:
                raise EmbeddingError("embedding_input_invalid")
            return ((1.0, 0.0),) * len(inputs)

        provider.embed.side_effect = embed
        result = embed_units(provider, ((bad, bad), (good,)), None)
        assert result[0].code == "embedding_input_invalid"
        assert result[1] == ((1.0, 0.0),)
        assert provider.embed.call_count == 3

    @pytest.mark.parametrize(
        "code", ["inference_cancelled", "inference_timeout", "embedding_compute_busy"]
    )
    @pytest.mark.parametrize("singleton", [False, True])
    def test_preserves_infrastructure_failure(self, code, singleton):
        provider = Mock()
        provider.embed.side_effect = (
            [EmbeddingError("embedding_output_mismatch"), EmbeddingError(code)]
            if singleton
            else EmbeddingError(code)
        )
        context = Mock()
        with pytest.raises(EmbeddingError, match=code):
            embed_units(
                provider, ((EmbeddingInput("text", text="a"),),), None, context=context
            )
        context.remaining.assert_called_once()
        assert provider.embed.call_args.kwargs == {"context": context}

    def test_rejects_mismatched_singleton_output(self):
        provider = Mock()
        provider.embed.return_value = ()
        result = embed_units(provider, ((EmbeddingInput("text", text="a"),),), None)
        assert result[0].code == "embedding_output_mismatch"
