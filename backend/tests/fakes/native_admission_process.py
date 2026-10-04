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
        "role", choices=("reserve", "orphan", "guarded-orphan", "inherit", "render")
    )
    parser.add_argument("directory", type=Path)
    parser.add_argument("--descriptor", type=int)
    parser.add_argument("--ticket", type=Path)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    if args.role == "render":
        import signal

        from app.bootstrap.native_resources import configure
        from app.core.config import _overlay
        from app.modules.media.mesh_contracts import ThumbnailRequest
        from app.modules.media.mesh_isolation import generate

        def stop(_signal, _frame):
            raise SystemExit(1)

        signal.signal(signal.SIGTERM, stop)
        _overlay["max_render_jobs"] = 2
        configure(args.directory)
        result = generate(ThumbnailRequest(args.source, include_thumbnail=False))
        print(json.dumps({"geometry": result.geometry}), flush=True)
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
                except (FileNotFoundError, json.JSONDecodeError):
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
