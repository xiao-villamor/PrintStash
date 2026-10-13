"""Prepared view units retain their result association across provider batches."""

from unittest.mock import Mock, call

from printstash_core.inference import EmbeddingInput, EmbeddingProvider, EmbeddingSpace

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
