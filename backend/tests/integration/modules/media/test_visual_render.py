"""Real child rendering is bounded and cannot outlive a cancelled caller."""

import pytest
from printstash_core.inference import EmbeddingError, EmbeddingSpace
from printstash_core.inference.context import InferenceContext
from printstash_core.search.visual_inputs import VisualRecipe

from app.modules.media import mesh_isolation, native_process, visual_render
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
    original = mesh_isolation.subprocess.Popen

    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(mesh_isolation.subprocess, "Popen", spawn)
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
            native_process,
            "process_tree_rss_bytes",
            lambda _pid: native_process.native_memory_budget_bytes() + 1,
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
        from pathlib import Path

        from app.core.cancellation import OperationCancelled, cancellation_scope
        from app.modules.media.worker_bootstrap import command

        source, recipe, processes = render_case
        pids = tmp_path / "pids"

        monkeypatch.setattr(
            visual_render,
            "worker_command",
            lambda _module, _args, budget: command(
                "tests.fakes.mesh_bootstrap_probe", ["tree_wait", str(pids)], budget
            ),
        )
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


class TestSupervision:
    def test_inference_withdrawal_reaps_the_tree(
        self, render_case, monkeypatch, tmp_path
    ):
        import json
        from pathlib import Path

        from app.modules.media.worker_bootstrap import command

        source, recipe, processes = render_case
        pids = tmp_path / "cancelled-pids"
        monkeypatch.setattr(
            visual_render,
            "worker_command",
            lambda _module, _args, budget: command(
                "tests.fakes.mesh_bootstrap_probe", ["tree_wait", str(pids)], budget
            ),
        )
        with pytest.raises(EmbeddingError, match="inference_cancelled"):
            visual_render.render(
                source,
                file_type="stl",
                recipe=recipe,
                context=InferenceContext.bounded(10, cancelled=pids.exists),
            )
        assert processes[0].poll() is not None
        assert all(
            not Path(f"/proc/{pid}").exists() for pid in json.loads(pids.read_text())
        )

    def test_refuses_reply_before_failed_exit(self, render_case, monkeypatch):
        import sys

        source, recipe, processes = render_case
        command = [
            sys.executable,
            "-c",
            "import os, struct, time; "
            "data=b'RGB1'+struct.pack('!HHB',32,32,7)+bytes(32*32*3*7); "
            "os.write(1,struct.pack('!I',len(data))+data); "
            "time.sleep(0.1); raise SystemExit(3)",
        ]
        monkeypatch.setattr(visual_render, "worker_command", lambda *_args: command)
        with pytest.raises(EmbeddingError, match="embedding_render_failed"):
            visual_render.render(
                source,
                file_type="stl",
                recipe=recipe,
                context=InferenceContext.bounded(10),
            )
        assert processes[0].poll() is not None

    def test_refuses_delayed_trailing_output(self, render_case, monkeypatch):
        import sys

        source, recipe, processes = render_case
        command = [
            sys.executable,
            "-c",
            "import os, struct, time; "
            "data=b'RGB1'+struct.pack('!HHB',32,32,7)+bytes(32*32*3*7); "
            "os.write(1,struct.pack('!I',len(data))+data); "
            "time.sleep(0.1); os.write(1,b'trailing')",
        ]
        monkeypatch.setattr(visual_render, "worker_command", lambda *_args: command)
        with pytest.raises(EmbeddingError, match="embedding_output_invalid"):
            visual_render.render(
                source,
                file_type="stl",
                recipe=recipe,
                context=InferenceContext.bounded(10),
            )
        assert processes[0].poll() is not None

    def test_admission_wait_honours_the_callers_deadline(self, render_case):
        from concurrent.futures import ThreadPoolExecutor

        from app.runtime.native_runtime import admit

        source, recipe, processes = render_case
        capacity = native_process.native_capacity()
        with (
            admit(capacity, capacity, checkpoint=lambda: None),
            ThreadPoolExecutor(1) as executor,
        ):
            result = executor.submit(
                visual_render.render,
                source,
                file_type="stl",
                recipe=recipe,
                context=InferenceContext.bounded(0.1),
            )
            with pytest.raises(EmbeddingError, match="inference_timeout"):
                result.result(timeout=5)

        assert processes == []

    def test_closed_stdout_does_not_disable_deadline(self, render_case, monkeypatch):
        import time

        from app.modules.media.worker_bootstrap import command

        source, recipe, processes = render_case

        monkeypatch.setattr(
            visual_render,
            "worker_command",
            lambda _module, _args, budget: command(
                "tests.fakes.mesh_bootstrap_probe", ["close_stdout_wait"], budget
            ),
        )
        started = time.monotonic()
        with pytest.raises(EmbeddingError, match="inference_timeout"):
            visual_render.render(
                source,
                file_type="stl",
                recipe=recipe,
                context=InferenceContext.bounded(0.5),
            )
        assert time.monotonic() - started < 5
        assert processes[0].poll() is not None
