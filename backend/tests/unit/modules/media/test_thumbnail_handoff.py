"""Host admission accounts for owned packets independently of parser credits."""

import json
import struct
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from printstash_core.mesh.rasterizer import RenderedPixels

from app.core.cancellation import OperationCancelled
from app.modules.media import compute_render
from app.modules.media import thumbnail_handoff as handoff
from app.modules.media.thumbnail_packet import (
    MAX_PACKET_BYTES,
    PacketDescriptor,
    validate_payload,
)
from app.runtime.native_admission import Resources


def payload(**changes):
    header = {
        "vertices": 3,
        "faces": 1,
        "normals": 3,
        "normal_mode": "gpu-visual-v2",
        "width": 640,
        "height": 480,
        "views": [None],
        "matte": False,
    }
    header.update(changes)
    encoded = json.dumps(header).encode()
    return struct.pack("!I", len(encoded)) + encoded + bytes(48)


class TestThumbnailPacket:
    def test_accepts_the_thumbnail_layout(self):
        assert validate_payload(payload(), 640, 480) == 1

    @pytest.mark.parametrize(
        "values",
        [
            {"views": [None, None]},
            {"matte": True},
            {"width": 320},
            {"normal_mode": "invalid"},
            {"faces": 2},
        ],
    )
    def test_refuses_other_render_contracts(self, values):
        with pytest.raises(ValueError):
            validate_payload(payload(**values), 640, 480)

    @pytest.mark.parametrize(
        "packet",
        [
            b"",
            b"1234",
            struct.pack("!I", 4097) + b"{}",
            payload()[:-1],
            payload() + b"x",
        ],
    )
    def test_refuses_invalid_framing(self, packet):
        with pytest.raises(ValueError):
            validate_payload(packet, 640, 480)

    @pytest.mark.parametrize(
        "values",
        [
            (4, "a" * 64, 1),
            (MAX_PACKET_BYTES + 1, "a" * 64, 1),
            (100, "X" * 64, 1),
            (100, "a" * 64, 0),
        ],
    )
    def test_refuses_invalid_descriptors(self, values):
        with pytest.raises(ValueError):
            PacketDescriptor(*values)


class TestHostWindow:
    def test_preserves_system_headroom(self):
        physical = 8 * 1024**3
        capacity = handoff.host_capacity(physical, physical // 2, 0.25)
        assert capacity == Resources(4, 384 * 1024**2)
        assert capacity.bytes <= (physical - physical // 2 - physical // 4) // 4
        assert 4 * handoff.resident_bytes(640, 480) <= capacity.bytes

    @pytest.mark.parametrize(
        "physical,geometry,fraction",
        [(None, 1, 0.25), (0, 1, 0.25), (1024, 1024, 0.25), (1024, 512, float("nan"))],
    )
    def test_refuses_unavailable_headroom(self, physical, geometry, fraction):
        assert handoff.host_capacity(physical, geometry, fraction) is None

    def test_covers_raw_readback_before_fallback(self):
        from printstash_core.mesh.preview_profile import PREVIEW_PROFILE

        raw = 640 * 480 * 4 * PREVIEW_PROFILE.supersample_for(640) ** 2
        assert handoff.resident_bytes(640, 480) >= MAX_PACKET_BYTES + 3 * raw


class TestHandoffCompletion:
    @pytest.fixture
    def owner(self):
        plan = handoff.HandoffPlan(640, 480, Resources(1, 100), Resources(4, 400))
        return handoff.ThumbnailHandoff(plan, Path("workspace"), time.monotonic() + 60)

    def test_retains_codec_credit_until_cancellation_returns(self, monkeypatch, owner):
        active = []
        packet = PacketDescriptor(100, "a" * 64, 1)
        monkeypatch.setattr(handoff, "read_packet", lambda *a, **k: b"packet")
        monkeypatch.setattr(
            compute_render,
            "render_packet",
            lambda *a, **k: RenderedPixels(640, 480, bytes(640 * 480 * 4)),
        )

        @contextmanager
        def admission(request, capacity, *, checkpoint):
            assert request == Resources(1, 1)
            active.append(True)
            try:
                yield
            finally:
                active.pop()

        monkeypatch.setattr(handoff, "admission", admission)

        def encode(*a, **k):
            assert active == [True]
            raise OperationCancelled()

        monkeypatch.setattr(handoff, "encode_pixels", encode)
        with pytest.raises(OperationCancelled):
            owner.complete(
                packet,
                Path("workspace"),
                (1, 2),
                fallback=lambda: pytest.fail("cancelled encoder fell back"),
            )
        assert active == []

    def test_retains_credit_while_a_cancelled_codec_is_still_running(
        self, monkeypatch, owner
    ):
        import threading

        entered, returned, cancelled, finished = (threading.Event() for _ in range(4))
        active = []
        errors = []
        monkeypatch.setattr(handoff, "read_packet", lambda *a, **k: b"packet")
        monkeypatch.setattr(
            compute_render,
            "render_packet",
            lambda *a, **k: RenderedPixels(640, 480, bytes(640 * 480 * 4)),
        )

        @contextmanager
        def admission(*a, **k):
            active.append(True)
            try:
                yield
            finally:
                active.pop()

        def check():
            if cancelled.is_set():
                raise OperationCancelled()

        def encode(*a, **k):
            entered.set()
            assert returned.wait(5)
            return b"encoded"

        def complete():
            try:
                owner.complete(
                    PacketDescriptor(100, "a" * 64, 1),
                    Path("workspace"),
                    (1, 2),
                    fallback=lambda: pytest.fail("cancelled codec recovered"),
                )
            except OperationCancelled as exc:
                errors.append(exc)
            finally:
                finished.set()

        monkeypatch.setattr(handoff, "admission", admission)
        monkeypatch.setattr(handoff, "checkpoint", check)
        monkeypatch.setattr(handoff, "encode_pixels", encode)
        worker = threading.Thread(target=complete)
        worker.start()
        try:
            assert entered.wait(5)
            cancelled.set()
            assert active == [True]
            assert not finished.is_set()
        finally:
            returned.set()
            worker.join(timeout=5)
        assert not worker.is_alive()
        assert len(errors) == 1
        assert active == []

    @pytest.mark.parametrize(
        "error",
        [
            OperationCancelled(),
            TimeoutError("thumbnail_deadline"),
            ValueError("compute_geometry_invalid"),
        ],
    )
    def test_does_not_recover_terminal_failures(self, monkeypatch, owner, error):
        monkeypatch.setattr(handoff, "read_packet", lambda *a, **k: b"packet")

        def render(*a, **k):
            raise error

        monkeypatch.setattr(compute_render, "render_packet", render)
        with pytest.raises(type(error)):
            owner.complete(
                PacketDescriptor(100, "a" * 64, 1),
                Path("workspace"),
                (1, 2),
                fallback=lambda: pytest.fail("terminal error fell back"),
            )


class TestExceptionalCustody:
    @pytest.mark.parametrize("error_type", [ValueError, OperationCancelled])
    @pytest.mark.parametrize("stage", ["dispatch", "fallback", "publication"])
    def test_clears_owned_buffers_before_releasing_host_credit(
        self, monkeypatch, error_type, stage
    ):
        import weakref

        references = []

        class Owner:
            def __init__(self, value):
                self.value = value

        def fail(payload):
            retained = Owner(payload)
            references.append(weakref.ref(retained))
            raise RuntimeError("inner")

        def render(payload, **kwargs):
            try:
                fail(payload)
            except RuntimeError as cause:
                raise error_type("outer") from cause

        @contextmanager
        def host_credit(*args, **kwargs):
            try:
                yield
            finally:
                assert references and all(
                    reference() is None for reference in references
                )

        monkeypatch.setattr(handoff.preparation_runtime, "reserve_host", host_credit)
        monkeypatch.setattr(handoff, "read_packet", lambda *a, **k: bytes(1024 * 1024))
        monkeypatch.setattr(
            compute_render,
            "render_packet",
            render if stage == "dispatch" else lambda *a, **k: None,
        )
        plan = handoff.HandoffPlan(640, 480, Resources(1, 100), Resources(4, 400))
        with pytest.raises(error_type) as caught:
            with handoff.reserve(plan, Path("workspace")) as owner:

                def generate():
                    result = owner.complete(
                        PacketDescriptor(100, "a" * 64, 1),
                        Path("workspace"),
                        (1, 2),
                        fallback=(lambda: render(b"fallback"))
                        if stage == "fallback"
                        else lambda: b"encoded",
                    )
                    if stage == "publication":
                        render(result)

                generate()
        assert isinstance(caught.value.__cause__, RuntimeError)
        assert caught.value.__traceback__ is not None
        assert all(reference() is None for reference in references)
