"""Completed RGBA encoding is bounded and never publishes withdrawn work."""

import io
import time
from unittest.mock import Mock

import pytest
from PIL import Image
from printstash_core.mesh.rasterizer import RenderedPixels

from app.core.cancellation import OperationCancelled, cancellation_scope
from app.modules.media.thumbnail_pixels import check_deadline, encode_pixels


def pixels(width=320, height=None, *, alpha=1, border=None):
    height = round(width * 3 / 4) if height is None else height
    rgba = bytearray(bytes([3, 5, 7, 0]) * (width * height))
    offset = ((height // 2) * width + width // 2) * 4
    rgba[offset : offset + 4] = bytes([190, 80, 40, alpha])
    if border is not None:
        index = {
            "top": width // 2,
            "bottom": (height - 1) * width + width // 2,
            "left": (height // 2) * width,
            "right": (height // 2 + 1) * width - 1,
        }[border]
        rgba[index * 4 + 3] = 1
    return RenderedPixels(width, height, bytes(rgba))


class TestEncodePixels:
    @pytest.mark.parametrize("width", [320, 1280])
    def test_preserves_decoded_alpha(self, width):
        frame = pixels(width)

        encoded = encode_pixels(frame, deadline=time.monotonic() + 30)

        with Image.open(io.BytesIO(encoded)) as image:
            assert image.format == "WEBP"
            assert image.size == (frame.width, frame.height)
            assert image.convert("RGBA").getchannel("A").tobytes() == frame.rgba[3::4]
        assert type(encoded) is bytes

    def test_uses_the_shared_lossy_encoding_recipe(self, monkeypatch):
        save = Image.Image.save
        settings = []

        def observe(image, fp, format=None, **params):
            settings.append((format, params))
            return save(image, fp, format, **params)

        monkeypatch.setattr(Image.Image, "save", observe)

        encode_pixels(pixels(), deadline=time.monotonic() + 30)

        assert settings == [
            (
                "WEBP",
                {
                    "lossless": False,
                    "exact": False,
                    "alpha_quality": 100,
                    "method": 2,
                    "quality": 85,
                },
            )
        ]

    @pytest.mark.parametrize("border", ["top", "bottom", "left", "right"])
    def test_refuses_foreground_at_the_frame_border(self, border, monkeypatch):
        save = Mock()
        monkeypatch.setattr(Image.Image, "save", save)

        with pytest.raises(ValueError, match="thumbnail_frame_invalid"):
            encode_pixels(pixels(border=border), deadline=time.monotonic() + 30)
        save.assert_not_called()

    def test_refuses_an_empty_frame(self):
        with pytest.raises(ValueError, match="thumbnail_empty"):
            encode_pixels(pixels(alpha=0), deadline=time.monotonic() + 30)

    @pytest.mark.parametrize("width,height", [(319, 239), (1281, 961), (320, 239)])
    def test_refuses_dimensions_outside_the_thumbnail_contract(self, width, height):
        with pytest.raises(ValueError, match="thumbnail_frame_invalid"):
            encode_pixels(pixels(width, height), deadline=time.monotonic() + 30)

    def test_preserves_codec_failure(self, monkeypatch):
        def fail(*args, **kwargs):
            raise OSError("encoder failed")

        monkeypatch.setattr(Image.Image, "save", fail)

        with pytest.raises(OSError, match="encoder failed"):
            encode_pixels(pixels(), deadline=time.monotonic() + 30)


class TestEncodingWithdrawal:
    def test_refuses_cancellation_before_encoding(self, monkeypatch):
        save = Mock()
        monkeypatch.setattr(Image.Image, "save", save)

        with cancellation_scope(lambda: True):
            with pytest.raises(OperationCancelled):
                encode_pixels(pixels(), deadline=time.monotonic() + 30)
        save.assert_not_called()

    @pytest.mark.parametrize(
        "deadline",
        [0.0, float("inf"), float("nan")],
        ids=["expired", "infinite", "nan"],
    )
    def test_refuses_invalid_deadlines_before_encoding(self, deadline, monkeypatch):
        save = Mock()
        monkeypatch.setattr(Image.Image, "save", save)

        with pytest.raises(TimeoutError, match="thumbnail_deadline"):
            encode_pixels(pixels(), deadline=deadline)
        save.assert_not_called()

    def test_withdraws_output_after_cancellation_during_encoding(self, monkeypatch):
        withdrawn = [False]

        def encode(image, fp, **kwargs):
            withdrawn[0] = True
            fp.write(b"completed-codec-output")

        monkeypatch.setattr(Image.Image, "save", encode)
        with cancellation_scope(lambda: withdrawn[0]):
            with pytest.raises(OperationCancelled):
                encode_pixels(pixels(), deadline=time.monotonic() + 30)

    def test_withdraws_output_completed_after_the_deadline(self, monkeypatch):
        clock = [10.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])

        def encode(image, fp, **kwargs):
            clock[0] = 20.0
            fp.write(b"completed-codec-output")

        monkeypatch.setattr(Image.Image, "save", encode)
        with pytest.raises(TimeoutError, match="thumbnail_deadline"):
            encode_pixels(pixels(), deadline=15.0)


class TestDeadlinePolling:
    def test_limits_withdrawal_reads_during_repeated_polls(self, monkeypatch):
        clock = [10.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        withdrawn = Mock(return_value=False)
        with cancellation_scope(withdrawn):
            for _ in range(100):
                check_deadline(11.0)
            assert withdrawn.call_count == 1
            clock[0] = 10.1
            for _ in range(100):
                check_deadline(11.0)
            assert withdrawn.call_count == 1
            clock[0] = 10.21
            check_deadline(11.0)
            assert withdrawn.call_count == 2

    def test_deadline_expires_between_cancellation_probes(self, monkeypatch):
        clock = [10.0]
        monkeypatch.setattr(time, "monotonic", lambda: clock[0])
        withdrawn = Mock(return_value=False)
        with cancellation_scope(withdrawn):
            check_deadline(10.05)
            clock[0] = 10.06
            with pytest.raises(TimeoutError, match="thumbnail_deadline"):
                check_deadline(10.05)
        assert withdrawn.call_count == 1

    def test_boundary_observes_cancellation_inside_the_poll_interval(self, monkeypatch):
        monkeypatch.setattr(time, "monotonic", lambda: 10.0)
        withdrawn = Mock(return_value=False)
        with cancellation_scope(withdrawn):
            check_deadline(11.0)
            withdrawn.return_value = True
            check_deadline(11.0)
            assert withdrawn.call_count == 1
            with pytest.raises(OperationCancelled):
                check_deadline(11.0, force=True)
        assert withdrawn.call_count == 2
