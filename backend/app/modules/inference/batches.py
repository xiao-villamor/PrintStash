"""Batch already-prepared units while preserving independent publication results."""

from printstash_core.inference import EmbeddingError


def embed_units(provider, units, space, *, context=None):
    """No parsing, waiting or publishing here; callers retain their own fences."""
    if not 1 <= len(units) <= 8 or any(not unit for unit in units):
        raise EmbeddingError("embedding_batch_budget")
    flattened = [(owner, item) for owner, unit in enumerate(units) for item in unit]
    if (
        len(flattened) > 16
        or sum(len(item.rgb or b"") + len(item.points or b"") for _, item in flattened)
        > 12 * 1024**2
    ):
        raise EmbeddingError("embedding_input_budget")

    def embed(inputs):
        if context is None:
            return provider.embed(inputs, space)
        return provider.embed(inputs, space, context=context)

    vectors = [[] for _ in units]
    errors = {}
    for start in range(0, len(flattened), 8):
        members = flattened[start : start + 8]
        if context is not None:
            context.remaining()
        try:
            result = embed(tuple(item for _, item in members))
            if len(result) != len(members):
                raise EmbeddingError("embedding_output_mismatch")
            for (owner, _), vector in zip(members, result, strict=True):
                vectors[owner].append(vector)
        except EmbeddingError as exc:
            if exc.code in {
                "inference_cancelled",
                "inference_timeout",
                "embedding_compute_busy",
            }:
                raise
            # One malformed member cannot invalidate other prepared units. Retry
            # only this bounded batch as singletons, under the same context.
            for owner, item in members:
                if owner in errors:
                    continue
                try:
                    result = embed((item,))
                    if len(result) != 1:
                        raise EmbeddingError("embedding_output_mismatch")
                    vectors[owner].append(result[0])
                except EmbeddingError as error:
                    if error.code in {
                        "inference_cancelled",
                        "inference_timeout",
                        "embedding_compute_busy",
                    }:
                        raise
                    errors[owner] = error
    return tuple(
        errors[index] if index in errors else tuple(rows)
        for index, rows in enumerate(vectors)
    )
