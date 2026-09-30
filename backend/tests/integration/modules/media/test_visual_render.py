"""Real child rendering is bounded and cannot outlive a cancelled caller."""

import pytest
from printstash_core.inference import EmbeddingError, EmbeddingSpace
from printstash_core.inference.context import InferenceContext
from printstash_core.search.visual_inputs import VisualRecipe

from app.modules.media import mesh_processing, visual_render
from tests.factories.geometry import tetrahedron


@pytest.fixture
def render_case(tmp_path, monkeypatch):
    source = tmp_path / "part.stl"
    source.write_bytes(tetrahedron().export(file_type="stl"))
    recipe = VisualRecipe.for_space(
        VisualRecipe.space(
            EmbeddingSpace("clip", "v1", 3, "text_image", "test"),
            image_size=32,
            profile="multiview",
        )
    )
    processes = []
    original = visual_render._spawn

    def spawn(*args):
        process = original(*args)
        processes.append(process)
        return process

    monkeypatch.setattr(visual_render, "_spawn", spawn)
    return source, recipe, processes


class TestVisualRender:
    def test_renders_step_without_database_access_in_the_child(self, render_case):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

        source, recipe, processes = render_case
        source = source.with_suffix(".step")
        writer = STEPControl_Writer()
        writer.Transfer(BRepPrimAPI_MakeBox(10, 20, 30).Shape(), STEPControl_AsIs)
        writer.Write(str(source))
        result = visual_render.render(
            source,
            file_type="step",
            recipe=recipe,
            context=InferenceContext.bounded(20),
        )
        assert len(result.views) == 6
        assert processes[0].poll() is not None

    def test_completes_one_isolated_multiview_pass(self, render_case):
        source, recipe, processes = render_case
        result = visual_render.render(
            source, file_type="stl", recipe=recipe, context=InferenceContext.bounded(10)
        )
        assert len(result.views) == 6
        assert len(result.thumbnail.rgb) == 32 * 32 * 3
        assert processes[0].poll() is not None
        assert list(source.parent.iterdir()) == [source]

    def test_kills_the_child_at_the_callers_deadline(self, render_case):
        source, recipe, processes = render_case
        with pytest.raises(EmbeddingError, match="inference_timeout"):
            visual_render.render(
                source,
                file_type="stl",
                recipe=recipe,
                context=InferenceContext.bounded(0.01),
            )
        assert processes[0].poll() is not None

    def test_kills_the_child_at_the_shared_memory_ceiling(
        self, render_case, monkeypatch
    ):
        source, recipe, processes = render_case
        monkeypatch.setattr(
            mesh_processing,
            "process_tree_rss_bytes",
            lambda _pid: mesh_processing.native_memory_budget_bytes() + 1,
        )
        with pytest.raises(EmbeddingError, match="embedding_worker_oom"):
            visual_render.render(
                source,
                file_type="stl",
                recipe=recipe,
                context=InferenceContext.bounded(10),
            )
        assert processes[0].poll() is not None

    @pytest.mark.parametrize(
        "payload", [b"", b"garbage", b"RGB1\0\x20\0\x20\7", b"ERR1private secret/path"]
    )
    def test_rejects_incomplete_or_untrusted_worker_output(self, render_case, payload):
        _, recipe, _ = render_case
        with pytest.raises(
            EmbeddingError, match="embedding_(output_invalid|render_failed)"
        ):
            visual_render.decode_reply(payload, recipe)

    def test_cancellation_reaps_the_worker_tree(
        self, render_case, monkeypatch, tmp_path
    ):
        import json
        import subprocess
        from pathlib import Path

        from app.core.cancellation import OperationCancelled, cancellation_scope
        from app.modules.media.worker_bootstrap import command
        from tests.paths import BACKEND_DIR

        source, recipe, processes = render_case
        pids = tmp_path / "pids"

        def waiting(*_args, **_kwargs):
            process = subprocess.Popen(
                command(
                    "tests.fakes.mesh_bootstrap_probe",
                    ["tree_wait", str(pids)],
                    256 * 1024**2,
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                cwd=BACKEND_DIR,
            )
            processes.append(process)
            return process

        monkeypatch.setattr(visual_render, "_spawn", waiting)
        with cancellation_scope(pids.exists), pytest.raises(OperationCancelled):
            visual_render.render(
                source,
                file_type="stl",
                recipe=recipe,
                context=InferenceContext.bounded(10),
            )
        assert processes[0].poll() is not None
        for pid in json.loads(pids.read_text()):
            assert not Path(f"/proc/{pid}").exists()
