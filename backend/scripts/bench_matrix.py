"""Compare committed release images on a dedicated four-CPU benchmark runner.

All builds precede measurements. Containers share no host network or Docker
socket. PostgreSQL gets one quarter of each total CPU/memory profile. Timing
flags require review; only correctness and infrastructure failures fail this CLI.
"""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import re
import secrets
import shutil
import statistics
import subprocess
import tempfile
import tomllib
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
ORIGINAL = "4b9afeb92d4e7e24298454af38c6c76aeddec437"
POSTGRES = (
    "postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"
)
METRICS = {
    "source_saved_s": ("saved_seconds",),
    "complete_s": ("total_seconds",),
    "api_p95_ms": ("navigation_latency", "p95_ms"),
    "app_cpu_s": ("server_tree_cpu_seconds",),
    "app_rss_bytes": ("sampled_peak_server_tree_rss_bytes",),
    "container_peak_bytes": ("container_memory_peak_bytes",),
}

QUEUE_METRICS = {
    "complete_s": ("total_seconds",),
    "acceptance_p95_ms": ("acceptance", "p95_ms"),
    "claim_p95_ms": ("claim", "p95_ms"),
    "completion_p95_ms": ("completion", "p95_ms"),
    "idle_cpu_s": ("idle", "cpu_seconds"),
    "coordinator_cpu_s": ("coordinator_cpu_seconds",),
    "container_peak_bytes": ("container_memory_peak_bytes",),
}

GCODE_METRICS = {
    "complete_s": ("total_seconds",),
    "parse_p95_ms": ("parse_latency", "p95_ms"),
    "app_cpu_s": ("process_cpu_seconds",),
    "app_rss_bytes": ("process_peak_rss_bytes",),
    "container_peak_bytes": ("container_memory_peak_bytes",),
}

ARCHIVE_METRICS = {
    "complete_s": ("total_seconds",),
    "archive_p95_ms": ("archive_latency", "p95_ms"),
    "app_cpu_s": ("process_cpu_seconds",),
    "app_rss_bytes": ("process_peak_rss_bytes",),
    "container_peak_bytes": ("container_memory_peak_bytes",),
}

MESH_METRICS = {
    "complete_s": ("total_seconds",),
    "preview_p95_ms": ("preview_latency", "p95_ms"),
    "app_cpu_s": ("process_cpu_seconds",),
    "app_rss_bytes": ("process_peak_rss_bytes",),
    "container_peak_bytes": ("container_memory_peak_bytes",),
}

SIMILARITY_METRICS = {
    "complete_s": ("total_seconds",),
    "fingerprint_p95_ms": ("fingerprint_latency", "p95_ms"),
    "verification_p95_ms": ("verification_latency", "p95_ms"),
    "app_cpu_s": ("process_cpu_seconds",),
    "app_rss_bytes": ("process_peak_rss_bytes",),
    "container_peak_bytes": ("container_memory_peak_bytes",),
}

ACQUISITION_METRICS = {
    "complete_s": ("total_seconds",),
    "small_p95_ms": ("download_latency", "small", "p95_ms"),
    "large_p95_ms": ("download_latency", "large", "p95_ms"),
    "redirect_p95_ms": ("download_latency", "redirect", "p95_ms"),
    "app_cpu_s": ("process_cpu_seconds",),
    "app_rss_bytes": ("process_peak_rss_bytes",),
    "container_peak_bytes": ("container_memory_peak_bytes",),
}


def compare_queue_contracts(before: dict, after: dict) -> None:
    if any(
        report.get("measurement_protocol") != "durable-queue-steady-v1"
        for report in (before, after)
    ):
        raise ValueError("Queue comparison requires the steady-state protocol")
    for key in (
        "measurement_protocol",
        "scope",
        "database",
        "accepted_count",
        "completed_count",
        "rollback_orphans",
        "duplicate_claims",
        "fault_injection",
    ):
        if key not in before or key not in after or before[key] != after[key]:
            raise ValueError(f"Queue comparison contract differs or is missing: {key}")


def command(args: list[str], *, log: Path | None = None) -> str:
    if log is None:
        return subprocess.check_output(args, cwd=ROOT, text=True).strip()
    with log.open("w") as output:
        subprocess.run(
            args, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, check=True
        )
    return ""


def revision(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Benchmark revisions must be full lowercase commit hashes")
    if command(["git", "cat-file", "-t", value]) != "commit":
        raise ValueError("Benchmark revision must identify a commit")
    return value


def metric(report: dict, path: tuple[str, ...]) -> float:
    value: object = report
    for key in path:
        if not isinstance(value, dict) or key not in value:
            raise ValueError(f"Missing benchmark metric: {'.'.join(path)}")
        value = value[key]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"Invalid benchmark metric: {'.'.join(path)}")
    return float(value)


def comparison(
    before: list[dict],
    after: list[dict],
    *,
    queue: bool = False,
    gcode: bool = False,
    archive: bool = False,
    mesh: bool = False,
    similarity: bool = False,
    acquisition: bool = False,
) -> dict:
    if not before or len(before) != len(after):
        raise ValueError("Comparison requires complete paired runs")
    result = {}
    noisy = False
    if sum((queue, gcode, archive, mesh, similarity, acquisition)) > 1:
        raise ValueError("A benchmark case cannot use two measurement protocols")
    contract = (
        QUEUE_METRICS
        if queue
        else GCODE_METRICS
        if gcode
        else ARCHIVE_METRICS
        if archive
        else MESH_METRICS
        if mesh
        else SIMILARITY_METRICS
        if similarity
        else ACQUISITION_METRICS
        if acquisition
        else METRICS
    )
    for name, path in contract.items():
        left, right = (
            [metric(report, path) for report in side] for side in (before, after)
        )
        threshold = 10 if name.endswith(("cpu_s", "bytes")) else 5
        deltas = [100 * (b / a - 1) for a, b in zip(left, right, strict=True) if a]
        variation = max(
            statistics.stdev(values) / statistics.mean(values) * 100
            if len(values) > 1 and statistics.mean(values)
            else 0
            for values in (left, right)
        )
        noisy |= variation > threshold
        result[name] = {
            "before_median": statistics.median(left),
            "after_median": statistics.median(right),
            "paired_delta_percent": statistics.median(deltas) if deltas else None,
            "max_coefficient_of_variation_percent": variation,
            "review_threshold_percent": threshold,
            "pairs_above_threshold": sum(delta > threshold for delta in deltas),
        }
    return {"pairs": len(before), "noisy": noisy, "metrics": result}


def build(rev: str, tag: str, work: Path, evidence: Path) -> str:
    context = work / rev
    context.mkdir()
    with tempfile.TemporaryFile() as archive:
        subprocess.run(
            ["git", "archive", f"{rev}:backend"], cwd=ROOT, stdout=archive, check=True
        )
        archive.seek(0)
        subprocess.run(["tar", "-x", "-C", str(context)], stdin=archive, check=True)
    command(
        [
            "docker",
            "build",
            "--build-arg",
            "PRINTSTASH_VARIANT=full",
            "--label",
            f"org.opencontainers.image.revision={rev}",
            "-t",
            tag,
            str(context),
        ],
        log=evidence / f"build-{rev}.log",
    )
    return command(["docker", "image", "inspect", "--format", "{{.Id}}", tag])


def harness_context(work: Path, head: str) -> Path:
    context = work / "harness"
    context.mkdir()
    for name in (
        "bench_import.py",
        "bench_database.py",
        "bench_archive.py",
        "bench_gcode.py",
        "bench_mesh_preview.py",
        "bench_similarity.py",
        "bench_acquisition.py",
        "bench_queue.py",
    ):
        (context / name).write_text(
            command(["git", "show", f"{head}:backend/scripts/{name}"]) + "\n"
        )
    fixtures = context / "gcode-fixtures"
    fixtures.mkdir()
    for name in ("sample.gcode", "bgcode/prusaslicer.bgcode"):
        destination = fixtures / Path(name).name
        destination.write_bytes(
            subprocess.check_output(
                ["git", "show", f"{head}:backend/tests/fixtures/{name}"], cwd=ROOT
            )
        )
    lock = tomllib.loads(command(["git", "show", f"{head}:backend/uv.lock"]))
    package = next(item for item in lock["package"] if item["name"] == "psutil")
    hashes = " ".join(f"--hash={wheel['hash']}" for wheel in package["wheels"])
    (context / "requirements.txt").write_text(
        f"psutil=={package['version']} {hashes}\n"
    )
    (context / "Dockerfile").write_text(
        "ARG RELEASE_IMAGE\nFROM ${RELEASE_IMAGE}\nUSER root\n"
        "COPY requirements.txt /tmp/benchmark-requirements.txt\n"
        "RUN uv pip install --python /app/.venv/bin/python --no-deps --require-hashes "
        "--only-binary :all: -r /tmp/benchmark-requirements.txt "
        "&& uv pip check --python /app/.venv/bin/python\n"
        # The release installs core as a wheel, so its source COPY can have
        # non-traversable directories. Hashing those sources needs read access
        # in this instrumentation layer, without running the benchmark as root.
        "RUN chmod -R a+rX /app/packages\n"
        "COPY bench_import.py bench_database.py bench_archive.py bench_gcode.py bench_mesh_preview.py bench_similarity.py bench_acquisition.py bench_queue.py /app/scripts/\n"
        "COPY gcode-fixtures /app/scripts/gcode-fixtures\n"
        f"LABEL org.printstash.benchmark.harness-revision={head}\n"
    )
    return context


def database_counters(container: str) -> dict:
    cpu = command(["docker", "exec", container, "cat", "/sys/fs/cgroup/cpu.stat"])
    fields = dict(line.split() for line in cpu.splitlines())
    return {
        "cpu_usec": int(fields["usage_usec"]),
        "lifetime_peak_bytes": int(
            command(
                [
                    "docker",
                    "exec",
                    container,
                    "cat",
                    "/sys/fs/cgroup/memory.peak",
                ]
            )
        ),
    }


def container_measurement_script() -> str:
    """Record cgroup totals after an argv-safe container command finishes."""
    return (
        '"$@"; result=$?; '
        "awk '/^usage_usec / {print $2}' /sys/fs/cgroup/cpu.stat > \"$BENCH_CPU\"; "
        'cat /sys/fs/cgroup/memory.peak > "$BENCH_MEMORY_PEAK"; exit "$result"'
    )


def container_resources(output: Path) -> dict[str, float | int]:
    return {
        "container_cpu_seconds": int(output.with_suffix(".cpu-usec").read_text())
        / 1_000_000,
        "container_memory_peak_bytes": int(
            output.with_suffix(".memory-peak").read_text()
        ),
    }


@dataclass(frozen=True)
class Profile:
    """One total system budget and the isolated service used by its measurements."""

    evidence: Path
    dialect: str
    cpus: int
    network: str
    env_file: Path
    database_container: str | None

    def run(
        self, image: str, name: str, archive: dict, reference: Path | None = None
    ) -> dict:
        evidence, dialect, cpus = self.evidence, self.dialect, self.cpus
        network, env_file, db = self.network, self.env_file, self.database_container
        output = evidence / "runs" / f"{name}.json"
        app_cpus = cpus * 0.75 if db else cpus
        app_memory = int(cpus * 1024 * (0.75 if db else 1))
        args = [
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--cpus",
            str(app_cpus),
            "--memory",
            f"{app_memory}m",
            "--memory-swap",
            f"{app_memory}m",
            "--network",
            network if db else "none",
            "--mount",
            f"type=bind,source={evidence},target=/evidence",
        ]
        if db:
            args += ["--env-file", str(env_file)]
        queue = bool(archive.get("queue"))
        gcode = bool(archive.get("gcode"))
        archive_benchmark = bool(archive.get("archive_benchmark"))
        mesh = bool(archive.get("mesh_benchmark"))
        similarity_benchmark = bool(archive.get("similarity_benchmark"))
        acquisition_benchmark = bool(archive.get("acquisition_benchmark"))
        cli = [
            "/app/.venv/bin/python",
            (
                "/app/scripts/bench_queue.py"
                if queue
                else "/app/scripts/bench_archive.py"
                if archive_benchmark
                else "/app/scripts/bench_mesh_preview.py"
                if mesh
                else "/app/scripts/bench_similarity.py"
                if similarity_benchmark
                else "/app/scripts/bench_acquisition.py"
                if acquisition_benchmark
                else "/app/scripts/bench_gcode.py"
                if gcode
                else "/app/scripts/bench_import.py"
            ),
        ]
        if gcode:
            cli += ["--fixtures", "/app/scripts/gcode-fixtures"]
        elif (
            not queue
            and not archive_benchmark
            and not mesh
            and not similarity_benchmark
            and not acquisition_benchmark
        ):
            cli += [f"/evidence/corpus/{archive['name']}"]
        cli += ["--database", dialect, "--output", f"/evidence/runs/{name}.json"]
        if queue:
            cli += ["--jobs", "128", "--idle-seconds", "10"]
            if not archive.get("queue_recovery"):
                cli.append("--steady-state")
        elif (
            not gcode
            and not archive_benchmark
            and not mesh
            and not similarity_benchmark
            and not acquisition_benchmark
        ):
            cli += ["--timeout", "1800"]
        if (
            db
            and not gcode
            and not archive_benchmark
            and not mesh
            and not similarity_benchmark
            and not acquisition_benchmark
        ):
            cli += ["--postgres-admin-url-env", "PRINTSTASH_BENCH_POSTGRES"]
        if archive["similarity"]:
            cli += ["--similarity"]
        if reference and not queue:
            cli += ["--compare", f"/evidence/runs/{reference.name}"]
        # Arguments are positional, not interpolated into shell source.
        script = container_measurement_script()
        args += ["--env", f"BENCH_MEMORY_PEAK=/evidence/runs/{name}.memory-peak"]
        args += ["--env", f"BENCH_CPU=/evidence/runs/{name}.cpu-usec"]
        args += ["--entrypoint", "/bin/sh", image, "-c", script, "benchmark", *cli]
        previous = database_counters(db) if db else None
        command(args, log=output.with_suffix(".log"))
        report = json.loads(output.read_text())
        if queue and reference:
            compare_queue_contracts(json.loads(reference.read_text()), report)
        report.update(container_resources(output))
        if db and previous:
            current = database_counters(db)
            report["database_resources"] = {
                "cli_lifecycle_cpu_seconds": (
                    current["cpu_usec"] - previous["cpu_usec"]
                )
                / 1_000_000,
                "container_lifetime_peak_bytes": current["lifetime_peak_bytes"],
                "scope": "CLI lifecycle includes database migration and metadata inspection; memory peak is cumulative for this profile's service",
            }
        output.write_text(json.dumps(report, indent=2) + "\n")
        return report


def prepare_images(
    revisions: list[str], head: str, prefix: str, work: Path, evidence: Path
) -> dict:
    context = harness_context(work, head)
    images = {}
    for rev in dict.fromkeys(revisions):
        release = f"{prefix}:release-{rev[:12]}"
        image = f"{prefix}:harness-{rev[:12]}"
        release_id = build(rev, release, work, evidence)
        command(
            [
                "docker",
                "build",
                "--build-arg",
                f"RELEASE_IMAGE={release}",
                "-t",
                image,
                str(context),
            ],
            log=evidence / f"harness-{rev}.log",
        )
        images[rev] = {
            "release_tag": release,
            "release_id": release_id,
            "benchmark_tag": image,
            "benchmark_id": command(
                ["docker", "image", "inspect", "--format", "{{.Id}}", image]
            ),
        }
    (evidence / "images.json").write_text(json.dumps(images, indent=2) + "\n")
    return images


def prepare_corpus(image: str, evidence: Path, source: Path) -> dict:
    command(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--network",
            "none",
            "--cpus",
            "4",
            "--memory",
            "4g",
            "--mount",
            f"type=bind,source={source},target=/sources,readonly",
            "--mount",
            f"type=bind,source={evidence},target=/evidence",
            "-e",
            "PYTHONPATH=/sources",
            "--entrypoint",
            "/app/.venv/bin/python",
            image,
            "/sources/scripts/bench_corpus.py",
            "/evidence/corpus",
        ],
        log=evidence / "corpus.log",
    )
    return json.loads((evidence / "corpus/manifest.json").read_text())


def server_environment(work: Path, password: str) -> tuple[Path, Path]:
    postgres, benchmark = work / "postgres.env", work / "benchmark.env"
    for path, content in (
        (
            postgres,
            f"POSTGRES_USER=printstash\nPOSTGRES_PASSWORD={password}\nPOSTGRES_DB=postgres\n",
        ),
        (
            benchmark,
            f"PRINTSTASH_BENCH_POSTGRES=postgresql://printstash:{password}@postgres:5432/postgres\n",
        ),
    ):
        with path.open("x") as stream:
            path.chmod(0o600)
            stream.write(content)
    return postgres, benchmark


@contextmanager
def private_network(prefix: str):
    network = command(["docker", "network", "create", "--internal", prefix])
    try:
        yield network
    finally:
        command(["docker", "network", "rm", network])


@contextmanager
def postgres_service(network: str, cpus: int, env_file: Path):
    container = command(
        [
            "docker",
            "run",
            "--detach",
            "--rm",
            "--network",
            network,
            "--network-alias",
            "postgres",
            "--cpus",
            str(cpus / 4),
            "--memory",
            f"{cpus * 256}m",
            "--memory-swap",
            f"{cpus * 256}m",
            "--env-file",
            str(env_file),
            POSTGRES,
        ]
    )
    try:
        command(
            [
                "docker",
                "exec",
                container,
                "sh",
                "-c",
                "for attempt in $(seq 1 60); do pg_isready -U printstash -d postgres && exit 0; sleep 1; done; exit 1",
            ]
        )
        yield container
    finally:
        command(["docker", "stop", container])


def measure_case(
    profile: Profile, archive: dict, ancestor: str, head: str, images: dict
) -> dict:
    label = f"{profile.dialect}-{profile.cpus}cpu-{ancestor[:12]}-{Path(archive['name']).stem}"
    reference = None
    for side, rev in (("base", ancestor), ("head", head)):
        name = f"{label}-warmup-{side}"
        profile.run(images[rev]["benchmark_id"], name, archive, reference)
        if side == "base":
            reference = profile.evidence / "runs" / f"{name}.json"
    reports = {"base": [], "head": []}
    queue = bool(archive.get("queue"))
    gcode = bool(archive.get("gcode"))
    archive_benchmark = bool(archive.get("archive_benchmark"))
    mesh = bool(archive.get("mesh_benchmark"))
    similarity_benchmark = bool(archive.get("similarity_benchmark"))
    acquisition_benchmark = bool(archive.get("acquisition_benchmark"))
    for pair in range(14):
        if (
            pair == 7
            and not comparison(
                reports["base"],
                reports["head"],
                queue=queue,
                gcode=gcode,
                archive=archive_benchmark,
                mesh=mesh,
                similarity=similarity_benchmark,
                acquisition=acquisition_benchmark,
            )["noisy"]
        ):
            break
        order = (("base", ancestor), ("head", head))
        for side, rev in order if pair % 2 == 0 else reversed(order):
            name = f"{label}-pair{pair + 1:02d}-{side}"
            print(name, flush=True)
            reports[side].append(
                profile.run(images[rev]["benchmark_id"], name, archive, reference)
            )
    return {
        "label": label,
        "base": ancestor,
        "head": head,
        "database": profile.dialect,
        "total_cpus": profile.cpus,
        "total_memory_gib": profile.cpus,
        **comparison(
            reports["base"],
            reports["head"],
            queue=queue,
            gcode=gcode,
            archive=archive_benchmark,
            mesh=mesh,
            similarity=similarity_benchmark,
            acquisition=acquisition_benchmark,
        ),
    }


def write_comparisons(summaries: list[dict], evidence: Path) -> None:
    (evidence / "comparisons.json").write_text(json.dumps(summaries, indent=2) + "\n")
    table = [
        "# Import performance comparison",
        "",
        "Timing flags require review; passing this job proves corpus correctness, not performance acceptance.",
        "",
        "| Workload / database / profile | Pairs | Complete before / after (s) | Paired change | Noisy |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for row in summaries:
        value = row["metrics"]["complete_s"]
        table.append(
            f"| {row['label']} | {row['pairs']} | {value['before_median']:.3f} / {value['after_median']:.3f} | {value['paired_delta_percent']:+.2f}% | {row['noisy']} |"
        )
    (evidence / "comparison.md").write_text("\n".join(table) + "\n")


def preserve_images(images: dict, destination: Path) -> None:
    """Keep baseline bytes: rebuilding a floating base later is not equivalent."""
    destination.mkdir()
    (destination / "images.json").write_text(json.dumps(images, indent=2) + "\n")
    with gzip.open(
        destination / "release-images.tar.gz", "wb", compresslevel=1
    ) as target:
        process = subprocess.Popen(
            [
                "docker",
                "image",
                "save",
                *[image["release_tag"] for image in images.values()],
            ],
            stdout=subprocess.PIPE,
        )
        assert process.stdout is not None
        shutil.copyfileobj(process.stdout, target)
        process.stdout.close()
        if process.wait() != 0:
            raise RuntimeError("Could not preserve immutable release images")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--head", required=True)
    parser.add_argument("--base", default=ORIGINAL)
    parser.add_argument("--original", default=ORIGINAL)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", choices=("sqlite", "postgres"))
    parser.add_argument("--cpus", type=int, choices=(2, 4))
    args = parser.parse_args()
    head, base, original = map(revision, (args.head, args.base, args.original))
    for ancestor in (base, original):
        command(["git", "merge-base", "--is-ancestor", ancestor, head])
    if len(os.sched_getaffinity(0)) < 4:
        raise RuntimeError(
            "The comparison requires a dedicated runner with at least four CPUs"
        )
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    evidence = output / "evidence"
    evidence.mkdir()
    (evidence / "runs").mkdir()
    prefix = f"printstash-benchmark-{uuid4().hex[:12]}"
    password = secrets.token_urlsafe(32)
    summaries = []
    with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
        work = Path(temporary)
        try:
            images = prepare_images(
                [original, base, head], head, prefix, work, evidence
            )
            # Retain immutable image bytes even if a later correctness check
            # rejects a workload; successful cases still need reproducible inputs.
            preserve_images(images, output / "images")
            corpus = prepare_corpus(images[head]["release_id"], evidence, work / head)
            pg_env, bench_env = server_environment(work, password)
            with private_network(prefix) as network:
                for dialect in (
                    (args.database,) if args.database else ("sqlite", "postgres")
                ):
                    for cpus in (args.cpus,) if args.cpus else (2, 4):
                        service = (
                            postgres_service(network, cpus, pg_env)
                            if dialect == "postgres"
                            else nullcontext(None)
                        )
                        with service as container:
                            profile = Profile(
                                evidence, dialect, cpus, network, bench_env, container
                            )
                            for ancestor in dict.fromkeys((original, base)):
                                cases = corpus["archives"] + [
                                    {
                                        "name": "gcode-parse",
                                        "gcode": True,
                                        "similarity": False,
                                    },
                                    {
                                        "name": "archive-extract",
                                        "archive_benchmark": True,
                                        "similarity": False,
                                    },
                                    {
                                        "name": "mesh-preview",
                                        "mesh_benchmark": True,
                                        "similarity": False,
                                    },
                                    {
                                        "name": "geometric-similarity",
                                        "similarity_benchmark": True,
                                        "similarity": False,
                                    },
                                    {
                                        "name": "url-acquisition",
                                        "acquisition_benchmark": True,
                                        "similarity": False,
                                    },
                                    {
                                        "name": "queue-steady",
                                        "queue": True,
                                        "similarity": False,
                                    },
                                ]
                                for archive in cases:
                                    summaries.append(
                                        measure_case(
                                            profile, archive, ancestor, head, images
                                        )
                                    )
                                    write_comparisons(summaries, evidence)
        finally:
            # Never publish the private test service's password, even after a
            # dependency error or resource cleanup failure.
            for path in evidence.rglob("*"):
                if path.is_file() and path.suffix in {".log", ".json", ".md"}:
                    path.write_text(
                        path.read_text(errors="replace").replace(password, "[redacted]")
                    )


if __name__ == "__main__":
    main()
