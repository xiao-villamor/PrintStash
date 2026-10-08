"""CLI/private admission and supervision for the disposable GPU research pilot."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack, chdir, redirect_stdout
from dataclasses import asdict
from pathlib import Path
from statistics import median

from scripts.gpu_render_measurement import (
    POLICY,
    Flow,
    Mode,
    OutputFormat,
    PilotSpec,
    exception_details,
    library_versions,
    measure,
)
from scripts.render_backend import Candidate
from scripts.render_qualification import revision, verify, working_tree_dirty
from scripts.render_statistics import summarize

REPLY_LIMIT = 4 * 1024**2


def _telemetry() -> dict[str, object]:
    executable = shutil.which("nvidia-smi")
    if executable is None and Path("/usr/lib/wsl/lib/nvidia-smi").is_file():
        executable = "/usr/lib/wsl/lib/nvidia-smi"
    scope = "device-wide baseline/end snapshot; not context attribution or VRAM peak"
    if executable is None:
        return {"status": "unavailable", "reason": "tool_absent", "scope": scope}
    try:
        reply = subprocess.run(
            [
                executable,
                "--query-gpu=index,uuid,name,driver_version,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return {
            "status": "observed" if reply.returncode == 0 else "unavailable",
            "returncode": reply.returncode,
            "snapshot_csv": reply.stdout,
            "diagnostic": reply.stderr,
            "scope": scope,
            "units": "memory fields MiB; N/A remains unavailable",
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "reason": type(exc).__name__, "scope": scope}


def _save(path: Path, report: dict[str, object]) -> None:
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    pending.replace(path)


def _worker(argv: list[str]) -> int:
    from app.modules.media.mesh_isolation import read_spec

    raw = read_spec(argv)
    raw.pop("overrides")
    raw["mode"] = Mode(raw["mode"])
    raw["flow"] = Flow(raw["flow"])
    raw["output_format"] = OutputFormat(raw["output_format"])
    raw["candidate"] = Candidate(raw["candidate"])
    spec = PilotSpec(**raw)
    reply = os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0)
    with open(os.devnull, "wb") as sink:
        os.dup2(sink.fileno(), sys.stdout.fileno())
    try:
        result = measure(spec)
    except Exception as exc:
        result = {
            "status": "failed",
            "reason": "worker_setup_failed",
            **exception_details(exc),
            "versions": library_versions(),
        }
    payload = json.dumps(result, allow_nan=False).encode()
    if len(payload) > REPLY_LIMIT:
        raise ValueError("gpu_pilot_reply_limit")
    reply.write(payload)
    reply.close()
    return 0


def main() -> int:
    if len(sys.argv) == 2 and sys.argv[1].startswith("{"):
        return _worker(sys.argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--case", default="sharp-cube")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--candidate",
        type=Candidate,
        choices=tuple(Candidate),
        default=Candidate.MODERNGL,
    )
    parser.add_argument("--backend", choices=("egl", "auto"))
    parser.add_argument("--adapter", dest="selector")
    parser.add_argument(
        "--allow-software",
        action="store_true",
        help="Conformance only; never qualifies physical acceleration",
    )
    parser.add_argument("--chunk-size", type=int, default=64000)
    parser.add_argument("--allocation-limit", type=int, default=512 * 1024**2)
    parser.add_argument("--frame-width", type=int, default=640)
    parser.add_argument("--frame-height", type=int, default=480)
    parser.add_argument("--views", type=int, default=1)
    parser.add_argument("--flow", type=Flow, choices=tuple(Flow), default=Flow.PREVIEW)
    parser.add_argument("--embedding-size", type=int, default=224)
    parser.add_argument(
        "--output-format",
        type=OutputFormat,
        choices=tuple(OutputFormat),
        default=OutputFormat.WEBP,
    )
    parser.add_argument("--timeout-seconds", type=float, default=120)
    parser.add_argument("--memory-budget-mb", type=int)
    parser.add_argument("--telemetry", action="store_true")
    parser.add_argument(
        "--manifest", type=Path, help="Predeclared corpus/software/quality policy"
    )
    args = parser.parse_args()
    if args.backend is None:
        args.backend = "auto" if args.candidate is Candidate.WGPU else "egl"
    if args.backend != ("auto" if args.candidate is Candidate.WGPU else "egl"):
        parser.error("wgpu uses auto; ModernGL uses egl")
    if not 1 <= args.trials <= 100 or not 1 <= args.views <= 6:
        parser.error("trials must be 1..100 and views 1..6")
    if (
        min(args.chunk_size, args.allocation_limit, args.frame_width, args.frame_height)
        < 1
    ):
        parser.error("positive chunk/allocation/dimensions required")
    if not 0 < args.timeout_seconds <= 3600 or (
        args.memory_budget_mb is not None and args.memory_budget_mb <= 0
    ):
        parser.error("finite positive timeout/budget required")
    if not 32 <= args.embedding_size <= 512:
        parser.error("embedding-size must be 32..512")
    if args.flow is not Flow.ANALYTIC and args.views != 1:
        parser.error("--views requires --flow analytic")
    if args.flow is Flow.MULTIVIEW and (
        (args.frame_width, args.frame_height) != (640, 480)
        or args.output_format is not OutputFormat.WEBP
    ):
        parser.error("multiview requires 640x480 WEBP thumbnail")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    source = (
        args.source.absolute()
        if args.source
        else args.output_dir.absolute()
        / "sources"
        / (args.case + (".stl" if args.case == "real-benchy" else ".3mf"))
    )
    args.output_dir = args.output_dir.absolute()
    if args.manifest is not None:
        if args.source is None:
            parser.error("--manifest requires an existing --source")
        verify(json.loads(args.manifest.read_text()), source, args.flow)
    with tempfile.TemporaryDirectory(prefix="gpu-pilot-vault-") as temporary:
        from scripts.bench_mesh_pipeline import (
            configure_private_vault,
            export_private_settings,
        )

        inherited = {
            key: value for key, value in os.environ.items() if key.startswith("VAULT_")
        }
        configure_private_vault(Path(temporary))
        try:
            with chdir(temporary), redirect_stdout(sys.stderr), ExitStack() as teardown:
                from app.core.config import settings
                from app.runtime.inference_resources import bind_pool as bind_inference
                from app.runtime.native_runtime import bind_pool
                from app.runtime.preparation_runtime import bind_pools

                teardown.callback(bind_pool, bind_pool(None))
                teardown.callback(bind_inference, bind_inference(None))
                teardown.callback(bind_pools, bind_pools(None))
                export_private_settings(settings)
                report = _run(args, source)
        finally:
            for key in tuple(os.environ):
                if key.startswith("VAULT_"):
                    del os.environ[key]
            os.environ.update(inherited)
    print(
        json.dumps(
            {
                "report": str(args.output_dir / "report.json"),
                "decision": report["decision"],
            }
        )
    )
    return 1 if report["decision"] == "declined" else 0


def _run(args: argparse.Namespace, source: Path) -> dict[str, object]:
    from app.core.cancellation import checkpoint
    from app.modules.media.mesh_isolation import (
        MeshWorkerError,
        runtime_overrides,
        supervise_result,
    )
    from app.modules.media.native_budget import (
        MeshSource,
        RasterCodec,
        RasterWork,
        estimate_sources,
    )
    from app.modules.media.native_execution import admission
    from app.modules.media.native_process import native_capacity
    from app.modules.media.worker_bootstrap import WorkerLifecycle, command
    from app.runtime.native_admission import Resources

    capacity = native_capacity()
    amount = estimate_sources(
        capacity,
        (MeshSource(source, source.suffix[1:]),),
        work=RasterWork(
            max(args.frame_width, args.embedding_size)
            if args.flow is Flow.MULTIVIEW
            else args.frame_width,
            max(args.frame_height, args.embedding_size)
            if args.flow is Flow.MULTIVIEW
            else args.frame_height,
            7 if args.flow is Flow.MULTIVIEW else args.views,
            RasterCodec.WEBP
            if args.output_format is OutputFormat.WEBP
            else RasterCodec.PNG,
        ),
    )
    if args.memory_budget_mb is not None:
        amount = Resources(1, args.memory_budget_mb * 1024**2)
    report: dict[str, object] = {
        "schema_version": 2,
        "candidate": args.candidate,
        "tested_commit": revision(),
        "working_tree_dirty": working_tree_dirty(),
        "scope": "private_gpu_pilot_no_production_adoption",
        "quality_policy": POLICY,
        "flow": args.flow,
        "output_format": args.output_format,
        "embedding_size": args.embedding_size,
        "requested_memory_bytes": amount.bytes,
        "host_capacity_bytes": capacity.bytes,
        "cells": [],
        "decision": "qualification_pending",
        "device_memory_scope": "requested allocations and optional device-wide snapshots; physical/context peak unavailable",
    }
    cells = []
    try:
        for mode in Mode:
            started = time.perf_counter()
            row: dict[str, object] = {"mode": mode, "status": "failed"}
            if args.telemetry:
                row["device_baseline"] = _telemetry()
            try:
                if amount.bytes > capacity.bytes:
                    raise ValueError("requested_memory_exceeds_native_capacity")
                spec = PilotSpec(
                    str(source),
                    str(args.output_dir.absolute() / mode),
                    None if args.source else args.case,
                    mode,
                    args.trials,
                    args.backend,
                    args.chunk_size,
                    args.allocation_limit,
                    args.frame_width,
                    args.frame_height,
                    args.views,
                    args.flow,
                    args.embedding_size,
                    args.output_format,
                    args.candidate,
                    args.selector,
                    args.allow_software,
                )
                with admission(amount, capacity, checkpoint=checkpoint) as permit:
                    result = supervise_result(
                        command(
                            "scripts.gpu_render_pilot",
                            [
                                json.dumps(
                                    {"overrides": runtime_overrides(), **asdict(spec)}
                                )
                            ],
                            amount.bytes,
                        ),
                        memory_budget=amount.bytes,
                        timeout_seconds=args.timeout_seconds,
                        permit=permit,
                        lifecycle=WorkerLifecycle.GUARDED,
                        reply_limit=REPLY_LIMIT,
                    )
                measured = json.loads(result.payload)
                failed = (
                    measured.get("status") == "failed"
                    or any(
                        item["status"] != "completed"
                        for item in measured.get("observations", ())
                    )
                    or any(not item["accepted"] for item in measured.get("quality", ()))
                    or bool(
                        measured.get("gpu_fixed_backend_chunk_determinism_failures")
                    )
                )
                row.update(
                    status="failed" if failed else "completed",
                    result=measured,
                    supervision=asdict(result.stats),
                )
            except MeshWorkerError as exc:
                row.update(
                    reason=exc.reason.value,
                    supervision=asdict(exc.supervision) if exc.supervision else None,
                )
            except (ValueError, OSError) as exc:
                row.update(reason="pilot_request_or_io_failed", diagnostic=str(exc))
            finally:
                row["complete_supervised_ms"] = (time.perf_counter() - started) * 1000
                if args.telemetry:
                    row["device_end"] = _telemetry()
                cells.append(row)
                report["cells"] = cells
                _save(args.output_dir / "report.json", report)
    except KeyboardInterrupt:
        report["cancelled"] = True
        report["decision"] = "declined"
        _save(args.output_dir / "report.json", report)
        return report
    gates = []
    for cell in cells:
        if cell["mode"] == Mode.CPU or "result" not in cell:
            continue
        data = cell["result"]
        samples = data.get("observations", ())
        cpu = [
            float(item["full_cold_source_visual_ms"])
            for item in samples
            if item["method"] == "cpu" and item["status"] == "completed"
        ]
        gpu = [
            float(item["full_cold_source_visual_ms"])
            for item in samples
            if item["method"] == "gpu" and item["status"] == "completed"
        ]
        devices = [
            item["device"]
            for item in samples
            if item["method"] == "gpu" and "device" in item
        ]
        hardware = bool(devices) and all(
            device["physical_acceleration"]
            if args.candidate is Candidate.WGPU
            else not any(
                token in device["GL_RENDERER"].lower()
                for token in ("llvmpipe", "softpipe", "swiftshader", "software")
            )
            for device in devices
        )
        speedup = (
            median(cpu) / median(gpu) if len(cpu) == len(gpu) == args.trials else None
        )
        gates.append(
            {
                "mode": cell["mode"],
                "cpu_statistics": summarize(cpu),
                "gpu_statistics": summarize(gpu),
                "minimum_observations_met": len(cpu) >= 30 and len(gpu) >= 30,
                "hardware_renderer": hardware,
                "full_cold_source_median_speedup": speedup,
                "accepted": cell["status"] == "completed"
                and hardware
                and speedup is not None
                and speedup >= 1.5
                and len(cpu) >= 30
                and len(gpu) >= 30,
            }
        )
    report["cost_quality_hardware_gates"] = gates
    report["decision"] = (
        "declined"
        if any(cell["status"] != "completed" for cell in cells)
        or not gates
        or not any(gate["accepted"] for gate in gates)
        else "qualification_pending"
    )
    report["remaining_qualification"] = [
        "30 fresh supervised processes per declared workload family on both verification devices",
        "Linux/Docker physical-device evidence tied to the tested commit",
        "upload acceptance through retrievable thumbnail (production Jobs remain CPU)",
        "protected analytic components",
        "driver/context-loss/OOM/cancellation recovery",
    ]
    _save(args.output_dir / "report.json", report)
    return report


if __name__ == "__main__":
    raise SystemExit(main())
