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


class TestNativeCanary:
    @pytest.mark.parametrize("valid", [True, False])
    def test_validates_postprocess_readback(self, monkeypatch, valid):
        import sys
        from types import SimpleNamespace

        from app.modules.media.gpu_postprocess import Postprocessor
        from tests.fakes.gpu_device import API, Buffer, Device

        device = Device()
        api = SimpleNamespace(
            BufferUsage=API.BufferUsage,
            MapMode=API.MapMode,
            TextureUsage=SimpleNamespace(COPY_DST=2, TEXTURE_BINDING=4),
        )
        monkeypatch.setitem(sys.modules, "wgpu", api)
        expected = []

        def write_texture(target, pixels, layout, size):
            from printstash_core.mesh.rasterizer import postprocess_rgba

            expected.append(postprocess_rgba(pixels.tobytes(), *owner.size[:2]).rgba)

        device.queue.write_texture = write_texture
        monkeypatch.setattr(
            Buffer, "read_mapped", lambda self: expected[-1] if valid else b"invalid"
        )
        owner = Postprocessor(device)
        if valid:
            assert owner.canary() is None
        else:
            with pytest.raises(ValueError, match="compute_postprocess_canary"):
                owner.canary()
            owner.close()
        assert all(buffer.destroyed for buffer in device.buffers)
