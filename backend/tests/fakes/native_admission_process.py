"""Real process roles for local resource-credit lifetime assertions."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from app.runtime.native_admission import LocalResourcePool, NativePermit, Resources


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "role",
        choices=(
            "reserve",
            "orphan",
            "guarded-orphan",
            "inherit",
            "render",
            "render-worker",
        ),
    )
    parser.add_argument("directory", type=Path)
    parser.add_argument("--descriptor", type=int)
    parser.add_argument("--ticket", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--ready", type=Path)
    parser.add_argument("--release", type=Path)
    parser.add_argument("--capacity-bytes", type=int)
    parser.add_argument("--spec")
    args = parser.parse_args()
    if args.role == "render-worker":
        from app.core.cancellation import checkpoint
        from app.modules.media.mesh_protocol import GeometryOutput
        from app.modules.media.mesh_worker import main as render_worker
        from app.modules.media.thumbnail_engine import ThumbnailEngine
        from app.runtime.native_runtime import current_permit
        from tests.fakes.mesh_bootstrap_probe import _publish_readiness

        original = ThumbnailEngine.generate

        def virtual_size(field="VmSize:"):
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith(field):
                    return int(line.split()[1]) * 1024
            return None

        def generate(engine, request, *, on_output=None):
            entry_virtual_bytes = virtual_size()
            entry_peak_virtual_bytes = virtual_size("VmPeak:")

            def emit(output):
                if on_output is not None:
                    on_output(output)
                if isinstance(output, GeometryOutput):
                    permit = current_permit()
                    if permit is None:
                        raise RuntimeError("real mesh worker has no inherited permit")
                    _publish_readiness(
                        args.ready,
                        json.dumps(
                            {
                                "pid": os.getpid(),
                                "bytes": permit.resources.bytes,
                                "identity": permit.identity,
                                "ticket": str(permit.path),
                                "stage": "geometry",
                                "entry_virtual_bytes": entry_virtual_bytes,
                                "entry_peak_virtual_bytes": entry_peak_virtual_bytes,
                                "geometry_virtual_bytes": virtual_size(),
                                "geometry_peak_virtual_bytes": virtual_size("VmPeak:"),
                            }
                        ),
                    )
                    deadline = time.monotonic() + 30
                    while not args.release.exists():
                        checkpoint()
                        if time.monotonic() >= deadline:
                            raise TimeoutError(
                                "mixed workload test did not release geometry gate"
                            )
                        time.sleep(0.01)

            result = original(engine, request, on_output=emit)
            completed = json.loads(args.ready.read_text())
            completed.update(
                stage="completed",
                completed_virtual_bytes=virtual_size(),
                completed_peak_virtual_bytes=virtual_size("VmPeak:"),
            )
            _publish_readiness(args.ready, json.dumps(completed))
            return result

        ThumbnailEngine.generate = generate
        raise SystemExit(render_worker([args.spec]))
    if args.role == "render":
        import signal

        from app.bootstrap.native_resources import configure
        from app.core.config import _overlay
        from app.modules.media import mesh_isolation, native_process
        from app.modules.media.mesh_contracts import ThumbnailRequest, encode_geometry
        from app.modules.media.mesh_isolation import generate
        from app.modules.media.mesh_telemetry import encode_phase_stats

        def stop(_signal, _frame):
            raise SystemExit(1)

        signal.signal(signal.SIGTERM, stop)
        _overlay["max_render_jobs"] = 2
        configure(args.directory)
        if args.capacity_bytes is not None:
            native_process.native_capacity = lambda: Resources(2, args.capacity_bytes)
        gated = args.ready is not None
        if gated:
            original_command = mesh_isolation.worker_command

            def worker_command(module, arguments, budget):
                if module != "app.modules.media.mesh_worker":
                    return original_command(module, arguments, budget)
                return original_command(
                    "tests.fakes.native_admission_process",
                    [
                        "render-worker",
                        str(args.directory),
                        "--ready",
                        str(args.ready),
                        "--release",
                        str(args.release),
                        "--spec",
                        arguments[0],
                    ],
                    budget,
                )

            mesh_isolation.worker_command = worker_command
        result = generate(
            ThumbnailRequest(
                args.source,
                include_thumbnail=gated,
                width=32 if gated else None,
                height=32 if gated else None,
            )
        )
        print(
            json.dumps(
                {
                    "geometry": result.geometry,
                    "geometry_outcome": encode_geometry(result.geometry_outcome),
                    "phase_stats": encode_phase_stats(result.phase_stats),
                    "failure_reason": result.failure_reason.value
                    if result.failure_reason is not None
                    else None,
                    "peak_tree_rss_bytes": result.supervision.peak_tree_rss_bytes
                    if result.supervision is not None
                    else None,
                    "image_bytes": len(result.image or b""),
                    "image_is_png": (result.image or b"").startswith(
                        b"\x89PNG\r\n\x1a\n"
                    ),
                }
            ),
            flush=True,
        )
        return
    if args.role == "inherit":
        with NativePermit.inherit(args.descriptor, args.ticket) as permit:
            print(json.dumps({"bytes": permit.resources.bytes}), flush=True)
            time.sleep(60)
        return
    if args.role == "guarded-orphan":
        from app.modules.media.worker_bootstrap import command, launch_resources

        pool = LocalResourcePool(args.directory)
        resources = Resources(1, 256 * 1024**2)
        with pool.reserve(resources, resources, checkpoint=lambda: None) as permit:
            pids = args.directory / "native-pids.json"
            temporary = args.directory / "printstash-mesh-guarded"
            temporary.mkdir()
            inherited = launch_resources(permit)
            child = subprocess.Popen(
                command(
                    "tests.fakes.mesh_bootstrap_probe",
                    ["tree_wait", str(pids)],
                    resources.bytes,
                ),
                pass_fds=inherited.descriptors,
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={
                    **os.environ,
                    **inherited.environment,
                    "TMPDIR": str(temporary),
                },
            )
            deadline = time.monotonic() + 10
            while True:
                try:
                    json.loads(pids.read_text())
                    break
                except FileNotFoundError, json.JSONDecodeError:
                    if child.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError(
                            "native probe did not become ready"
                        ) from None
                    time.sleep(0.01)
            print(json.dumps({"child": child.pid}), flush=True)
            sys.stdin.readline()
        return
    pool = LocalResourcePool(args.directory)
    with pool.reserve(
        Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
    ) as permit:
        result = {"bytes": permit.resources.bytes}
        if args.role == "orphan":
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    __spec__.name,
                    "inherit",
                    str(args.directory),
                    "--descriptor",
                    str(permit.fileno),
                    "--ticket",
                    str(permit.path),
                ],
                pass_fds=(permit.fileno,),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                text=True,
            )
            assert child.stdout is not None
            assert json.loads(child.stdout.readline()) == {"bytes": 100}
            result["child"] = child.pid
        print(json.dumps(result), flush=True)
        sys.stdin.readline()


if __name__ == "__main__":
    main()
