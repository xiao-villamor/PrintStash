"""Deferred normals retain canonical fallback and bounded deterministic topology."""

from types import SimpleNamespace

import numpy as np
from printstash_core.mesh.render_geometry import (
    prepare_mesh,
    prepare_mesh_render,
)

from app.modules.media.gpu_normals import (
    canonical,
    defer,
)


def source():
    return SimpleNamespace(
        vertices=np.asarray([[0.0, 0, 0], [10, 0, 0], [0, 10, 0], [0, 0, 0]]),
        faces=np.asarray([[0, 1, 2], [3, 2, 1]], dtype=np.int64),
    )


def deferred():
    return defer(prepare_mesh(source()))


class TestCanonical:
    def test_preserves_canonical_fallback(self):
        original = source()
        prepared = defer(prepare_mesh(original))
        original.vertices[:] += 500
        original.faces[:] = 0
        result = canonical(prepared)
        expected = prepare_mesh_render(source())
        np.testing.assert_array_equal(result.position_ids, expected.position_ids)
        np.testing.assert_array_equal(result.corner_normals, expected.corner_normals)
        assert not hasattr(prepared, "corner_normals")
        assert result.vertices is prepared.vertices
        assert not result.corner_normals.flags.writeable

    def test_broker_fallback_does_not_grow_cached_descriptor(self):
        prepared = deferred()
        first, second = canonical(prepared), canonical(prepared)
        assert first is not second
        assert prepared._canonical is None
        assert canonical(first) is first

    def test_parser_memoization_requires_explicit_opt_in(self):
        prepared = deferred()
        result = canonical(prepared, cache=True)
        assert canonical(prepared, cache=True) is result
        assert canonical(prepared, cache=False) is not result
