"""Source preflight stays bounded and cannot guess unknown geometry complexity."""

import os
from pathlib import Path

import pytest

from app.modules.media import mesh_policy, native_budget
from app.runtime.native_admission import Resources
from app.runtime.native_runtime import admit
from tests.factories.content import binary_stl


class TestEstimateSources:
    def test_reads_binary_count_through_a_stable_descriptor(self, tmp_path):
        source = tmp_path / "mesh.stl"
        source.write_bytes(binary_stl(triangles=2))
        pool = Resources(4, 4 * 1024**3)

        with source.open("rb") as stream:
            alias = Path(f"/proc/self/fd/{stream.fileno()}")
            amount = native_budget.estimate_sources(
                pool, (native_budget.MeshSource(alias, "stl"),)
            )

        assert amount == Resources(1, 512 * 1024**2)

    @pytest.mark.parametrize(
        "file_type,payload",
        [
            pytest.param("stl", b"solid text\nendsolid\n", id="ascii"),
            pytest.param("stl", b"truncated", id="malformed"),
            pytest.param("3mf", b"PK", id="archive"),
            pytest.param("obj", b"v 0 0 0\n", id="polygon"),
        ],
    )
    def test_unmeasurable_input_reserves_whole_pool(self, tmp_path, file_type, payload):
        source = tmp_path / "source"
        source.write_bytes(payload)
        pool = Resources(4, 4 * 1024**3)

        assert native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, file_type),)
        ) == Resources(1, pool.bytes)

    def test_missing_source_does_not_authorize_an_optimistic_share(self, tmp_path):
        pool = Resources(4, 4 * 1024**3)

        assert native_budget.estimate_sources(
            pool, (native_budget.MeshSource(tmp_path / "missing", "stl"),)
        ) == Resources(1, pool.bytes)

    def test_large_file_preflight_does_not_scan_facets(self, tmp_path):
        source = tmp_path / "large.stl"
        source.write_bytes(
            binary_stl(triangles=1)[:80] + (20_000_000).to_bytes(4, "little")
        )
        with source.open("r+b") as stream:
            stream.truncate(84 + 20_000_000 * 50)
        pool = Resources(4, 4 * 1024**3)

        assert (
            native_budget.estimate_sources(
                pool, (native_budget.MeshSource(source, "stl"),)
            ).bytes
            == pool.bytes
        )
        assert os.stat(source).st_blocks * 512 < 1024**2


class TestAdmittedGeometry:
    def test_loader_uses_its_weighted_allowance(self, monkeypatch):
        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
        amount = Resources(1, 512 * 1024**2)
        with admit(amount, Resources(4, 4 * 1024**3), checkpoint=lambda: None):
            cap = mesh_policy.ram_triangle_cap(".stl")

        assert cap == (512 * 1024**2) // 3000


class _CapturedWork(Exception):
    pass


class TestCallerProfiles:
    @pytest.mark.parametrize(
        "include_thumbnail,include_fingerprint,output_format,width,height,expected",
        [
            pytest.param(
                False, False, "PNG", None, None, "geometry", id="geometry-only"
            ),
            pytest.param(True, False, "PNG", 32, 24, "png", id="small-png"),
            pytest.param(True, False, "WEBP", None, None, "webp", id="configured-webp"),
            pytest.param(
                False, True, "PNG", None, None, "analysis", id="analysis-only"
            ),
            pytest.param(
                True, True, "WEBP", 320, 240, "analysis-webp", id="analysis-with-webp"
            ),
        ],
    )
    def test_mesh_request_forwards_its_actual_work(
        self,
        tmp_path,
        monkeypatch,
        include_thumbnail,
        include_fingerprint,
        output_format,
        width,
        height,
        expected,
    ):
        from app.core.config import _overlay
        from app.modules.media import mesh_isolation
        from app.modules.media.mesh_contracts import ThumbnailRequest

        monkeypatch.setitem(_overlay, "model_thumbnail_width", 640)
        path = tmp_path / "mesh.stl"
        path.write_bytes(binary_stl(triangles=12))
        captured = []

        def observe(module, spec, *, sources, work, on_chunk=None):
            captured.append((sources, work))
            raise _CapturedWork

        monkeypatch.setattr(mesh_isolation, "_run_worker", observe)
        request = ThumbnailRequest(
            path,
            include_thumbnail=include_thumbnail,
            include_fingerprint=include_fingerprint,
            output_format=output_format,
            width=width,
            height=height,
        )
        with pytest.raises(_CapturedWork):
            mesh_isolation.generate(request)
        ((sources, work),) = captured
        assert sources == (native_budget.MeshSource(path, ".stl"),)
        if expected == "geometry":
            assert work == native_budget.GeometryWork()
        elif expected == "analysis":
            assert work == native_budget.AnalysisWork()
        else:
            raster = native_budget.RasterWork(
                width or 640,
                height or 480,
                1,
                native_budget.RasterCodec.WEBP
                if "webp" in expected
                else native_budget.RasterCodec.PNG,
            )
            assert work == (
                native_budget.AnalysisWork(raster)
                if expected == "analysis-webp"
                else raster
            )

    @pytest.mark.parametrize("consumer", ["embedding", "verification", "conversion"])
    def test_other_native_callers_forward_explicit_work(
        self, tmp_path, monkeypatch, consumer
    ):
        from app.modules.media import (
            embedding_isolation,
            mesh_isolation,
            stl_isolation,
            verification_isolation,
        )

        source = tmp_path / "mesh.3mf"
        source.write_bytes(b"PK")
        captured = []

        def observe(module, spec, *, sources, work):
            captured.append(work)
            raise _CapturedWork

        monkeypatch.setattr(mesh_isolation, "run_worker", observe)
        with pytest.raises(_CapturedWork):
            if consumer == "embedding":
                embedding_isolation.embedding_views(
                    source,
                    file_type="3mf",
                    component_index=0,
                    image_size=128,
                    triangle_cap=1000,
                )
            elif consumer == "verification":
                verification_isolation.verify_paths(
                    source, source, first_type="3mf", second_type="3mf"
                )
            else:
                stl_isolation.to_stl_bytes(source, file_type="3mf")
        (work,) = captured
        if consumer == "embedding":
            assert work == native_budget.RasterWork(
                128, 128, 6, native_budget.RasterCodec.RGB
            )
        elif consumer == "verification":
            assert work == native_budget.AnalysisWork()
        else:
            assert work == native_budget.GeometryWork()

    def test_source_estimation_retains_the_requested_profile(self, tmp_path):
        source = tmp_path / "cube.stl"
        source.write_bytes(binary_stl(triangles=12))
        pool = Resources(4, 4 * 1024**3)
        work = native_budget.AnalysisWork()
        assert native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "stl"),), work=work
        ) == Resources(1, 1024**3)

    def test_nested_work_retains_the_existing_hard_claim(self, tmp_path, monkeypatch):
        from app.modules.media import mesh_isolation, native_process
        from app.runtime.native_runtime import current_permit

        source = tmp_path / "cube.stl"
        source.write_bytes(binary_stl(triangles=12))
        pool = Resources(2, 2 * 1024**3)
        amount = Resources(1, 512 * 1024**2)
        monkeypatch.setattr(native_process, "native_capacity", lambda: pool)
        observed = []

        def no_reestimate(*args, **kwargs):
            pytest.fail("nested work requested a second allowance")

        def command(module, arguments, ceiling):
            observed.append(ceiling)
            return ["probe"]

        def supervise(
            command, *, memory_budget, timeout_seconds, permit, lifecycle, on_chunk
        ):
            assert current_permit() is permit
            assert memory_budget == amount.bytes
            assert permit.resources == amount
            raise _CapturedWork

        monkeypatch.setattr(mesh_isolation, "estimate_sources", no_reestimate)
        monkeypatch.setattr(mesh_isolation, "worker_command", command)
        monkeypatch.setattr(mesh_isolation, "supervise_result", supervise)
        with admit(amount, pool, checkpoint=lambda: None):
            with pytest.raises(_CapturedWork):
                mesh_isolation._run_worker(
                    "app.modules.media.mesh_worker",
                    {},
                    sources=(native_budget.MeshSource(source, "stl"),),
                    work=native_budget.AnalysisWork(),
                )
        assert observed == [amount.bytes]

    def test_streaming_preview_accounts_for_larger_framebuffer(
        self, tmp_path, monkeypatch
    ):
        from app.modules.media import mesh_isolation, native_process, stl_streaming
        from app.runtime.native_runtime import current_permit

        source = tmp_path / "cube.stl"
        source.write_bytes(binary_stl(triangles=12))
        monkeypatch.setattr(
            native_process, "native_capacity", lambda: Resources(2, 2 * 1024**3)
        )
        from app.modules.media import worker_bootstrap

        observed = []
        bootstrap_ceilings = []
        original_command = worker_bootstrap.command

        def command(module, arguments, ceiling):
            bootstrap_ceilings.append(ceiling)
            return original_command(module, arguments, ceiling)

        def observe(command, *, memory_budget, permit, **kwargs):
            assert current_permit() is permit
            observed.append((permit.resources.bytes, memory_budget))
            raise _CapturedWork

        monkeypatch.setattr(worker_bootstrap, "command", command)
        monkeypatch.setattr(mesh_isolation, "supervise_result", observe)
        with pytest.raises(_CapturedWork):
            stl_streaming.render_stl_preview_isolated(source, width=1280, height=960)
        # The work claim and pre-import AS ceiling include the framebuffer.
        # Streaming retains its independently stricter 256 MiB RSS limit.
        expected = 512 * 1024**2 + 58_982_400
        assert observed == [(expected, 256 * 1024**2)]
        assert bootstrap_ceilings == [expected]
        assert current_permit() is None
        assert source.read_bytes() == binary_stl(triangles=12)
