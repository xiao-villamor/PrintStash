"""Owned visual packets outlive parsing without retaining native execution credits."""

from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import time

import pytest
from PIL import Image
from printstash_core.mesh.rasterizer import RenderedPixels

from app.core.cancellation import OperationCancelled
from app.modules.media import mesh_isolation, thumbnail_finalization, thumbnail_staging
from app.modules.media.mesh_contracts import (
    PreviewCoverage,
    ThumbnailFailureReason,
    ThumbnailRequest,
)
from app.modules.media.mesh_protocol import GeometryOutput, ThumbnailOutput
from app.modules.media.thumbnail_staging import (
    PACKET_NAME,
    StagedRender,
    ThumbnailStaging,
)
from app.runtime import native_runtime, preparation_runtime
from app.runtime.native_admission import Resources
from tests.factories import content
from tests.paths import BACKEND_DIR


@pytest.fixture
def staged_request(tmp_path):
    source = tmp_path / "cube.stl"
    source.write_bytes(content.binary_stl())
    width, height = 64, 48
    amount = Resources(
        1, thumbnail_staging.resident_bytes(width, height) + 2 * source.stat().st_size
    )
    with preparation_runtime.reserve(amount, amount, checkpoint=lambda: None) as permit:
        with thumbnail_staging.reserve(
            preparation_runtime.workspace(permit), width, height
        ) as staging:
            yield (
                ThumbnailRequest(
                    source, width=width, height=height, output_format="WEBP"
                ),
                staging,
            )


class TestThumbnailHandoff:
    def test_dispatch_releases_parser_memory(self, staged_request, monkeypatch):
        request, staging = staged_request
        frames = []
        expected = bytes([18, 37, 51, 255]) * (64 * 48)

        def dispatch(wire, *, deadline):
            assert native_runtime.current_permit() is None
            assert preparation_runtime.current_visual_permit() is not None
            assert preparation_runtime.current_permit() is not None
            assert not any(isinstance(frame, ThumbnailOutput) for frame in frames)
            assert wire.face_count == 12
            assert deadline > time.monotonic()
            return RenderedPixels(64, 48, expected)

        monkeypatch.setattr(thumbnail_finalization, "render_staged", dispatch)
        result = mesh_isolation.generate(
            request, on_output=frames.append, staging=staging
        )

        assert [type(frame) for frame in frames] == [GeometryOutput, ThumbnailOutput]
        assert result.geometry["triangle_count"] == 12
        assert result.coverage.preview is PreviewCoverage.COMPLETE
        with Image.open(io.BytesIO(result.image)) as image:
            assert image.convert("RGBA").tobytes() == expected
        assert native_runtime.current_permit() is None

    def test_fallback_uses_owned_geometry_after_source_removal(
        self, staged_request, monkeypatch
    ):
        request, staging = staged_request

        def unavailable(wire, *, deadline):
            request.path.unlink()
            return None

        monkeypatch.setattr(thumbnail_finalization, "render_staged", unavailable)
        result = mesh_isolation.generate(request, staging=staging)

        assert not request.path.exists()
        assert result.geometry["triangle_count"] == 12
        assert result.coverage.preview is PreviewCoverage.COMPLETE
        with Image.open(io.BytesIO(result.image)) as image:
            assert image.size == (64, 48)
            assert image.getbbox() is not None

    def test_rejects_unaccepted_parser_completion(self, staged_request, monkeypatch):
        request, staging = staged_request
        frames = []
        run = mesh_isolation._run_worker

        def refuse(*args, **kwargs):
            run(*args, **kwargs)
            raise mesh_isolation.MeshWorkerError(ThumbnailFailureReason.WORKER_FAILED)

        monkeypatch.setattr(mesh_isolation, "_run_worker", refuse)
        monkeypatch.setattr(
            thumbnail_finalization,
            "render_staged",
            lambda *args, **kwargs: pytest.fail("unaccepted parser reply dispatched"),
        )
        with pytest.raises(mesh_isolation.MeshWorkerError):
            mesh_isolation.generate(request, staging=staging, on_output=frames.append)
        assert [type(frame) for frame in frames] == [GeometryOutput]

    def test_withdrawal_prevents_thumbnail_publication(
        self, staged_request, monkeypatch
    ):
        request, staging = staged_request
        frames = []

        def withdraw(*args, **kwargs):
            raise OperationCancelled()

        monkeypatch.setattr(thumbnail_finalization, "render_staged", withdraw)
        with pytest.raises(OperationCancelled):
            mesh_isolation.generate(request, staging=staging, on_output=frames.append)
        assert [type(frame) for frame in frames] == [GeometryOutput]
        assert native_runtime.current_permit() is None

    def test_preserves_failing_finalizer_supervision(self, staged_request, monkeypatch):
        from app.modules.media.mesh_telemetry import SupervisionStats, WorkerExitCause

        request, staging = staged_request
        supervise = mesh_isolation.supervise_result
        expected = SupervisionStats(
            "f" * 32, 120, 4096, 0, WorkerExitCause.MEMORY_LIMIT
        )

        def fail_finalizer(command, **kwargs):
            if "app.modules.media.thumbnail_finalize_worker" in command:
                error = mesh_isolation.MeshWorkerError(
                    ThumbnailFailureReason.RESOURCE_LIMIT
                )
                error.supervision = expected
                raise error
            return supervise(command, **kwargs)

        monkeypatch.setattr(mesh_isolation, "supervise_result", fail_finalizer)
        monkeypatch.setattr(
            thumbnail_finalization,
            "render_staged",
            lambda *args, **kwargs: RenderedPixels(64, 48, bytes(64 * 48 * 4)),
        )
        with pytest.raises(mesh_isolation.MeshWorkerError) as error:
            mesh_isolation.generate(request, staging=staging)
        assert error.value.supervision is expected
        assert native_runtime.current_permit() is None

    def test_keeps_the_original_finalization_deadline(
        self, staged_request, monkeypatch
    ):
        from types import SimpleNamespace

        request, staging = staged_request
        frames = []

        def expire(wire, *, deadline):
            monkeypatch.setattr(
                thumbnail_finalization,
                "time",
                SimpleNamespace(
                    monotonic=lambda: deadline + 1, monotonic_ns=time.monotonic_ns
                ),
            )
            return RenderedPixels(64, 48, bytes(64 * 48 * 4))

        monkeypatch.setattr(thumbnail_finalization, "render_staged", expire)
        with pytest.raises(mesh_isolation.MeshWorkerError) as error:
            mesh_isolation.generate(request, staging=staging, on_output=frames.append)
        assert error.value.reason is ThumbnailFailureReason.TIMEOUT
        assert [type(frame) for frame in frames] == [GeometryOutput]
        assert not (staging.directory / thumbnail_staging.PIXELS_NAME).exists()


class TestStagedPacketOwnership:
    @pytest.mark.parametrize("mutation", ["digest", "symlink", "directory"], ids=str)
    def test_rejects_changed_custody(self, tmp_path, mutation):
        directory = tmp_path / "owned"
        directory.mkdir()
        staging = ThumbnailStaging(directory, 64, 48)
        original = b"immutable geometry"
        packet = directory / PACKET_NAME
        packet.write_bytes(original)
        receipt = StagedRender(len(original), hashlib.sha256(original).hexdigest())
        if mutation == "digest":
            packet.write_bytes(b"x" * len(original))
        elif mutation == "symlink":
            target = tmp_path / "other"
            target.write_bytes(original)
            packet.unlink()
            packet.symlink_to(target)
        else:
            directory.rename(tmp_path / "original")
            directory.mkdir()
            (directory / PACKET_NAME).write_bytes(original)

        with pytest.raises((ValueError, OSError)):
            staging.read(receipt)

    def test_cleanup_releases_visual_custody(self, tmp_path):
        amount = Resources(1, thumbnail_staging.resident_bytes(64, 48))
        with preparation_runtime.reserve(
            amount, amount, checkpoint=lambda: None
        ) as permit:
            with pytest.raises(ValueError, match="abandoned"):
                with thumbnail_staging.reserve(
                    preparation_runtime.workspace(permit), 64, 48
                ) as staging:
                    (staging.directory / PACKET_NAME).write_bytes(b"unfinished")
                    raise ValueError("abandoned")
            assert not staging.directory.exists()
            assert preparation_runtime.current_visual_permit() is None

    def test_supervisor_keeps_numerical_imports_isolated(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; import app.modules.media.thumbnail_finalization; import app.modules.media.mesh_isolation; assert 'numpy' not in sys.modules; assert 'PIL.Image' not in sys.modules",
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
        assert result.returncode == 0, result.stderr


class TestStagingBootstrap:
    def test_encoder_avoids_application_configuration(self, tmp_path):
        import json

        expected = bytes([18, 37, 51, 255]) * (64 * 48)
        (tmp_path / thumbnail_staging.PIXELS_NAME).write_bytes(expected)
        spec = {
            "mode": "encode",
            "directory": str(tmp_path),
            "width": 64,
            "height": 48,
            "output_format": "WEBP",
            "face_chunk_size": 64000,
        }
        script = (
            "import sys; from app.modules.media.thumbnail_finalize_worker import main; "
            "main(sys.argv[1:]); "
            "assert not {'app.core.config', 'app.modules.media.mesh_isolation', "
            "'pydantic', 'numpy', 'trimesh'} & sys.modules.keys()"
        )

        reply = subprocess.run(
            [sys.executable, "-c", script, json.dumps(spec)],
            cwd=BACKEND_DIR,
            capture_output=True,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )

        assert reply.returncode == 0, reply.stderr.decode()
        with Image.open(io.BytesIO(reply.stdout)) as image:
            assert image.convert("RGBA").tobytes() == expected

    def test_transports_visual_credit_to_the_guarded_worker(self):
        from app.modules.media.native_execution import admission
        from app.modules.media.worker_bootstrap import WorkerLifecycle, command

        amount = Resources(1, 128 * 1024**2)
        with preparation_runtime.reserve(amount, amount, checkpoint=lambda: None):
            with preparation_runtime.visual_slot(100, checkpoint=lambda: None):
                with admission(amount, amount, checkpoint=lambda: None) as permit:
                    reply = mesh_isolation.supervise_result(
                        command(
                            "tests.fakes.mesh_bootstrap_probe",
                            ["visual_credit"],
                            amount.bytes,
                        ),
                        memory_budget=amount.bytes,
                        timeout_seconds=10,
                        permit=permit,
                        lifecycle=WorkerLifecycle.GUARDED,
                    )
        assert int(reply.payload) == 100


class TestNativePlan:
    def test_reuses_preflight_for_guarded_execution(self, staged_request, monkeypatch):
        request, staging = staged_request
        plan = mesh_isolation.plan_native(request)

        def repeated(*args, **kwargs):
            pytest.fail("source preflight ran twice")

        monkeypatch.setattr(mesh_isolation, "estimate_sources", repeated)
        monkeypatch.setattr(
            thumbnail_finalization, "render_staged", lambda *args, **kwargs: None
        )

        result = mesh_isolation.generate(request, staging=staging, native_plan=plan)

        assert result.geometry["triangle_count"] == 12
        assert result.coverage.preview is PreviewCoverage.COMPLETE
        assert native_runtime.current_permit() is None
