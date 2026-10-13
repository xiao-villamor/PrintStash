"""Resolution-dependent data is small and independent of model pixels."""

import numpy as np
import pytest

from app.modules.media.gpu_postprocess import coefficients, workspace_bytes


class TestCoefficients:
    @pytest.mark.parametrize("size", [1, 17, 640, 1280])
    def test_preserves_identity_sampling(self, size):
        kernel = coefficients(size, 1)
        np.testing.assert_array_equal(kernel[:, 0], np.arange(size))
        assert (kernel[:, 1] == 1).all()
        assert (kernel[:, 2] == 2**22).all()
        assert (kernel[:, 3:] == 0).all()

    @pytest.mark.parametrize("size", [1, 17, 640, 1280])
    def test_bounds_lanczos_support(self, size):
        kernel = coefficients(size, 2)
        assert (kernel[:, 0] >= 0).all()
        assert (kernel[:, 0] + kernel[:, 1] <= size * 2).all()
        assert (kernel[:, 1] <= 13).all()
        np.testing.assert_allclose(kernel[:, 2:].sum(axis=1), 2**22, atol=6, rtol=0)

    @pytest.mark.parametrize("size,factor", [(0, 2), (1281, 2), (64, 3)])
    def test_refuses_unsupported_dimensions(self, size, factor):
        with pytest.raises(ValueError, match="compute_postprocess_dimensions"):
            coefficients(size, factor)

    def test_accounts_for_postprocess_buffers(self):
        assert workspace_bytes(640, 480, 2) == 640 * 480 * 16 + 1120 * 60 + 16
