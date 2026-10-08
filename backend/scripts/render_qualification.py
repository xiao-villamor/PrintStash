"""Freeze qualification inputs before rendering; manifests can contain private paths."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
from pathlib import Path

from scripts.gpu_render_measurement import POLICY, Flow, library_versions


def fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            digest.update(block)
    return digest.hexdigest()


def revision() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2], text=True
    ).strip()


def working_tree_dirty() -> bool:
    return bool(
        subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=Path(__file__).resolve().parents[2],
            text=True,
        ).strip()
    )


def freeze(sources: list[Path], family: str, flow: Flow) -> dict[str, object]:
    if working_tree_dirty():
        raise ValueError("qualification_requires_committed_source")
    if not sources or not family.strip():
        raise ValueError("qualification_requires_declared_workload")
    if any(path.suffix.lower() not in (".stl", ".3mf") for path in sources):
        raise ValueError("qualification_requires_stl_or_3mf")
    return {
        "schema_version": 1,
        "tested_commit": revision(),
        "workload_family": family,
        "flow": flow,
        "quality_policy": POLICY,
        "minimum_observations": 30,
        "minimum_complete_flow_speedup": 1.5,
        "sources": [
            {"path": str(path.resolve()), "sha256": fingerprint(path)}
            for path in sources
        ],
        "versions": library_versions(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "lock_sha256": fingerprint(Path(__file__).resolve().parents[1] / "uv.lock"),
    }


def verify(manifest: dict[str, object], source: Path, flow: Flow) -> None:
    if (
        manifest["schema_version"] != 1
        or manifest["quality_policy"] != POLICY
        or manifest["flow"] != flow
        or manifest["minimum_observations"] != 30
        or manifest["minimum_complete_flow_speedup"] != 1.5
    ):
        raise ValueError("qualification_policy_changed")
    if (
        manifest["versions"] != library_versions()
        or manifest["python"] != platform.python_version()
    ):
        raise ValueError("qualification_software_changed")
    if working_tree_dirty() or manifest["tested_commit"] != revision():
        raise ValueError("qualification_commit_changed")
    if manifest["lock_sha256"] != fingerprint(
        Path(__file__).resolve().parents[1] / "uv.lock"
    ):
        raise ValueError("qualification_lock_changed")
    entries = manifest["sources"]
    if not isinstance(entries, list):
        raise ValueError("invalid_qualification_sources")
    matches = [item for item in entries if item["path"] == str(source.resolve())]
    if len(matches) != 1 or matches[0]["sha256"] != fingerprint(source):
        raise ValueError("qualification_source_changed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, action="append", required=True)
    parser.add_argument("--family", required=True)
    parser.add_argument("--flow", type=Flow, choices=tuple(Flow), default=Flow.PREVIEW)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # Exclusive creation prevents overwriting a predeclared gate after seeing output.
    with args.output.open("x") as stream:
        json.dump(freeze(args.source, args.family, args.flow), stream, indent=2)
        stream.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
