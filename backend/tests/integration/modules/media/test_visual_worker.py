"""The visual child emits only complete private frames for its caller."""

import struct

import pytest
from printstash_core.search.point_inputs import PointRecipe
from printstash_core.search.visual_inputs import VisualRecipe

from app.modules.media import visual_render, visual_worker
from tests.factories.geometry import tetrahedron


@pytest.fixture
def visual_message(tmp_path, monkeypatch):
    source = tmp_path / "private-source.stl"
    source.write_bytes(tetrahedron().export(file_type="stl"))
    destination = tmp_path / "reply"

    def execute(recipe, kind="stl", limit=None):
        if limit is not None:
            monkeypatch.setattr(visual_worker, "MAX_REPLY", limit)
        monkeypatch.setattr(
            visual_worker.sys, "argv", ["worker", str(source), kind, recipe]
        )
        # An owned descriptor stands in for stdout; main still duplicates and
        # redirects it itself, exactly as in the actual child process.
        with destination.open("w") as output:
            with monkeypatch.context() as patch:
                patch.setattr(visual_worker.sys, "stdout", output)
                status = visual_worker.main()
        return status, destination.read_bytes()

    return execute


class TestMain:
    @pytest.mark.parametrize("profile", ["thumbnail", "multiview", "point_cloud"])
    def test_frames_every_visual_profile(self, visual_message, profile):
        recipe = (
            PointRecipe("a" * 64, "b" * 64)
            if profile == "point_cloud"
            else VisualRecipe("a" * 64, 32, profile)
        )
        status, frame = visual_message(recipe.encode())
        assert status == 0
        assert struct.unpack("!I", frame[:4])[0] == len(frame) - 4
        result = visual_render.decode_reply(frame[4:], recipe)
        if profile == "point_cloud":
            assert len(result.views) == 1
            assert len(result.views[0].points) == 240000
        else:
            assert len(result.views) == (6 if profile == "multiview" else 1)
            assert len(result.thumbnail.rgb) == 32 * 32 * 3

    @pytest.mark.parametrize("limit", ["request", "reply"])
    def test_refuses_oversized_messages(self, visual_message, limit):
        status, frame = visual_message(
            " " * 1025
            if limit == "request"
            else VisualRecipe("a" * 64, 32, "thumbnail").encode(),
            limit=1 if limit == "reply" else None,
        )
        assert status == 2
        assert frame == b""

    @pytest.mark.parametrize("fault", ["recipe", "kind"])
    def test_sanitizes_failures(self, visual_message, fault):
        status, frame = visual_message(
            "private invalid JSON"
            if fault == "recipe"
            else VisualRecipe("a" * 64, 32, "thumbnail").encode(),
            kind="unsupported" if fault == "kind" else "stl",
        )
        assert status == 0
        assert struct.unpack("!I", frame[:4])[0] == len(frame) - 4
        assert frame[4:].startswith(b"ERR1")
        assert b"private" not in frame
        assert b"/" not in frame
