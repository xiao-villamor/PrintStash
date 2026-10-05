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


class TestVisualWorkAdmissionProfile:
    @pytest.mark.parametrize("profile", ["thumbnail", "multiview"])
    def test_visual_recipe_reserves_its_canonical_webp_work_envelope(
        self, render_case, monkeypatch, profile
    ):
        from dataclasses import replace

        source, recipe, processes = render_case
        recipe = replace(recipe, profile=profile)
        amounts = []
        original = mesh_isolation.supervise_result

        def supervise(*args, **kwargs):
            amounts.append(kwargs["permit"].resources)
            return original(*args, **kwargs)

        monkeypatch.setattr(mesh_isolation, "supervise_result", supervise)
        result = visual_render.render(
            source,
            file_type="stl",
            recipe=recipe,
            context=InferenceContext.bounded(20),
        )

        assert len(amounts) == 1
        # Policy bounds all render frames by the canonical thumbnail rectangle.
        # Additional multiview rectangles conservatively overestimate the actual
        # 32x32 RGB planes; their cost is incremental over one qualified frame.
        bounding_frame_count = 7 if profile == "multiview" else 1
        expected = 640 * 1024**2 + (bounding_frame_count - 1) * 640 * 480 * 64
        assert amounts[0].bytes == min(expected, native_process.native_capacity().bytes)
        assert amounts[0].slots == 1
        assert result.thumbnail is not None
        assert len(result.thumbnail.rgb) == 32 * 32 * 3
        assert len(result.views) == recipe.view_count
        assert all(len(view.rgb) == 32 * 32 * 3 for view in result.views)
        assert processes[0].poll() is not None

    def test_point_recipe_reserves_analysis_without_raster_work(
        self, render_case, monkeypatch
    ):
        from printstash_core.search.point_inputs import PointRecipe

        source, _, processes = render_case
        recipe = PointRecipe(encoder_space_hash="a" * 64, paired_space_hash="b" * 64)
        amounts = []
        original = mesh_isolation.supervise_result

        def supervise(*args, **kwargs):
            amounts.append(kwargs["permit"].resources)
            return original(*args, **kwargs)

        monkeypatch.setattr(mesh_isolation, "supervise_result", supervise)
        result = visual_render.render(
            source,
            file_type="stl",
            recipe=recipe,
            context=InferenceContext.bounded(20),
        )

        assert len(amounts) == 1
        assert amounts[0].bytes == min(
            1024 * 1024**2, native_process.native_capacity().bytes
        )
        assert amounts[0].slots == 1
        assert result.thumbnail is None
        assert len(result.views) == 1
        assert len(result.views[0].points) == 240000
        assert processes[0].poll() is not None

    def test_nested_visual_work_reuses_its_existing_admitted_envelope(
        self, render_case, monkeypatch
    ):
        from app.runtime.native_admission import Resources
        from app.runtime.native_runtime import admit

        source, recipe, processes = render_case
        capacity = native_process.native_capacity()
        amount = Resources(1, min(1024 * 1024**2, capacity.bytes))
        observed = []
        original = mesh_isolation.supervise_result

        def supervise(*args, **kwargs):
            observed.append(kwargs["permit"])
            return original(*args, **kwargs)

        monkeypatch.setattr(mesh_isolation, "supervise_result", supervise)
        with admit(amount, capacity, checkpoint=lambda: None) as permit:
            result = visual_render.render(
                source,
                file_type="stl",
                recipe=recipe,
                context=InferenceContext.bounded(20),
            )
        assert observed == [permit]
        assert len(result.views) == 6
        assert result.thumbnail is not None
        assert processes[0].poll() is not None
