#!/usr/bin/env python3
"""Compare PrintStash's durable queue with qualified Rust candidates.

Builds happen before measurement. Each run gets a fresh database, the requested
container budget, bounded work, and no retry. Crash recovery is recorded once
per implementation outside the seven-or-fourteen steady-state pairs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sqlite3
import sys
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.bench_matrix import (
    Profile,
    command,
    container_measurement_script,
    container_resources,
    database_counters,
    postgres_service,
    prepare_images,
    private_network,
    revision,
    server_environment,
)
from scripts.bench_queue_qualification import (
    IMPLEMENTATIONS,
    execution_order,
    summarize_completed_boundary,
    validate_profile,
)

ROOT = Path(__file__).resolve().parents[2]


def sqlite_database_bytes(path: Path) -> int:
    if not path.is_file():
        return 0
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        pages = connection.execute("PRAGMA page_count").fetchone()[0]
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    value = pages * page_size
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RuntimeError("SQLite size inspection returned an invalid value")
    return value


def postgres_database_bytes(container: str, database: str) -> int:
    if not re.fullmatch(r"queue_qualification_[0-9a-f]{32}", database):
        raise ValueError("PostgreSQL benchmark database name is invalid")
    value = command(
        [
            "docker",
            "exec",
            container,
            "psql",
            "--tuples-only",
            "--no-align",
            "--username",
            "printstash",
            "--dbname",
            "postgres",
            "--command",
            f"SELECT pg_database_size('{database}')",
        ]
    )
    result = int(value)
    if result < 0:
        raise RuntimeError("PostgreSQL size inspection returned an invalid value")
    return result


@dataclass(frozen=True)
class CandidateProfile:
    evidence: Path
    database: str
    cpus: int
    network: str
    image: str
    password: str
    database_container: str | None

    def run(self, candidate: str, name: str, *, recovery: bool) -> dict:
        if candidate not in {"apalis", "azums"}:
            raise ValueError(f"unsupported queue candidate: {candidate}")
        output = self.evidence / "runs" / f"{name}.json"
        root = self.evidence / "runs" / f"{name}-root"
        root.mkdir()
        app_cpus = self.cpus * 0.75 if self.database_container else self.cpus
        app_memory = int(self.cpus * 1024 * (0.75 if self.database_container else 1))
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
            self.network if self.database_container else "none",
            "--mount",
            f"type=bind,source={self.evidence},target=/evidence",
            "--env",
            "PRINTSTASH_QUEUE_QUALIFICATION_MODE=benchmark",
            "--env",
            f"PRINTSTASH_QUEUE_QUALIFICATION_CANDIDATE={candidate}",
            "--env",
            f"PRINTSTASH_QUEUE_QUALIFICATION_DATABASE={self.database}",
            "--env",
            f"PRINTSTASH_QUEUE_QUALIFICATION_ROOT=/evidence/runs/{root.name}",
            "--env",
            "PRINTSTASH_QUEUE_QUALIFICATION_JOBS=128",
            "--env",
            "PRINTSTASH_QUEUE_QUALIFICATION_WORKERS=1",
            "--env",
            "PRINTSTASH_QUEUE_QUALIFICATION_IDLE_SECONDS=10",
            "--env",
            f"PRINTSTASH_QUEUE_QUALIFICATION_RECOVERY={'true' if recovery else 'false'}",
            "--env",
            f"BENCH_CPU=/evidence/runs/{name}.cpu-usec",
            "--env",
            f"BENCH_MEMORY_PEAK=/evidence/runs/{name}.memory-peak",
        ]
        database_name = None
        before = 0
        if self.database_container:
            database_name = f"queue_qualification_{uuid4().hex}"
            command(
                [
                    "docker",
                    "exec",
                    self.database_container,
                    "createdb",
                    "--username",
                    "printstash",
                    database_name,
                ]
            )
            before = postgres_database_bytes(self.database_container, database_name)
            args += [
                "--env",
                "PRINTSTASH_QUEUE_QUALIFICATION_POSTGRES_URL="
                f"postgresql://printstash:{self.password}@postgres:5432/{database_name}",
            ]
        script = container_measurement_script()
        args += [
            "--entrypoint",
            "/bin/sh",
            self.image,
            "-c",
            script,
            "benchmark",
            "/usr/local/bin/queue-qualification",
        ]
        previous = (
            database_counters(self.database_container)
            if self.database_container
            else None
        )
        try:
            command(args, log=output.with_suffix(".log"))
            candidate_output = root / f"{candidate}-{self.database}-benchmark.json"
            report = json.loads(candidate_output.read_text())
            if self.database_container and database_name:
                after = postgres_database_bytes(
                    self.database_container, database_name
                )
            else:
                after = sqlite_database_bytes(root / f"{candidate}-benchmark.sqlite")
            report["database_growth_bytes"] = after - before
            if report["database_growth_bytes"] < 0:
                raise RuntimeError("candidate database shrank during measurement")
            report.update(container_resources(output))
            if self.database_container and previous:
                current = database_counters(self.database_container)
                report["database_resources"] = {
                    "cli_lifecycle_cpu_seconds": (
                        current["cpu_usec"] - previous["cpu_usec"]
                    )
                    / 1_000_000,
                    "container_lifetime_peak_bytes": current[
                        "lifetime_peak_bytes"
                    ],
                    "scope": "candidate lifecycle; database peak is cumulative for the profile",
                }
            output.write_text(json.dumps(report, indent=2) + "\n")
            return report
        finally:
            if self.database_container and database_name:
                command(
                    [
                        "docker",
                        "exec",
                        self.database_container,
                        "dropdb",
                        "--force",
                        "--username",
                        "printstash",
                        database_name,
                    ]
                )


def candidate_image(head: str, prefix: str, evidence: Path) -> dict[str, str]:
    tag = f"{prefix}:queue-candidates-{head[:12]}"
    command(
        [
            "docker",
            "build",
            "--label",
            f"org.opencontainers.image.revision={head}",
            "--tag",
            tag,
            str(ROOT / "backend/qualification/queue"),
        ],
        log=evidence / "build-queue-candidates.log",
    )
    return {
        "tag": tag,
        "id": command(["docker", "image", "inspect", "--format", "{{.Id}}", tag]),
    }


def write_report(summary: dict, output: Path) -> None:
    output.write_text(json.dumps(summary, indent=2) + "\n")
    rows = [
        "# Queue qualification performance comparison",
        "",
        "Correctness failures disqualify adoption regardless of timing. Timing thresholds identify review work; they do not override a failed contract.",
        "",
        "| Metric | Current | Apalis | Azums | Review threshold |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, metric in summary["metrics"].items():
        rows.append(
            f"| {name} | {metric['current']['median']:.4f} | "
            f"{metric['apalis']['median']:.4f} ({metric['apalis']['paired_delta_from_current_percent']:+.2f}%) | "
            f"{metric['azums']['median']:.4f} ({metric['azums']['paired_delta_from_current_percent']:+.2f}%) | "
            f"{metric['review_threshold_percent']}% |"
        )
    output.with_suffix(".md").write_text("\n".join(rows) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--head", required=True)
    parser.add_argument("--database", required=True, choices=("sqlite", "postgres"))
    parser.add_argument("--cpus", required=True, type=int, choices=(2, 4))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    validate_profile(cpus=args.cpus, memory_gib=args.cpus, pairs=7, jobs=128)
    head = revision(args.head)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    evidence = output / "evidence"
    evidence.mkdir()
    (evidence / "runs").mkdir()
    prefix = f"printstash-queue-qualification-{uuid4().hex[:12]}"
    password = secrets.token_urlsafe(32)

    with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
        work = Path(temporary)
        try:
            current_images = prepare_images([head], head, prefix, work, evidence)
            rust_image = candidate_image(head, prefix, evidence)
            (evidence / "queue-images.json").write_text(
                json.dumps(
                    {
                        "revision": head,
                        "current": current_images[head],
                        "candidates": rust_image,
                    },
                    indent=2,
                )
                + "\n"
            )
            pg_env, bench_env = server_environment(work, password)
            with private_network(prefix) as network:
                service = (
                    postgres_service(network, args.cpus, pg_env)
                    if args.database == "postgres"
                    else nullcontext(None)
                )
                with service as database_container:
                    current = Profile(
                        evidence,
                        args.database,
                        args.cpus,
                        network,
                        bench_env,
                        database_container,
                    )
                    candidates = CandidateProfile(
                        evidence,
                        args.database,
                        args.cpus,
                        network,
                        rust_image["id"],
                        password,
                        database_container,
                    )
                    archive = {
                        "name": "queue-steady",
                        "queue": True,
                        "similarity": False,
                    }
                    for implementation in IMPLEMENTATIONS:
                        name = f"warmup-{implementation}"
                        if implementation == "current":
                            current.run(
                                current_images[head]["benchmark_id"], name, archive
                            )
                        else:
                            candidates.run(implementation, name, recovery=False)

                    reports: dict[str, list[dict]] = {
                        implementation: [] for implementation in IMPLEMENTATIONS
                    }
                    for pair in range(14):
                        boundary = summarize_completed_boundary(reports)
                        if pair == 7 and boundary is not None and not boundary["noisy"]:
                            break
                        for implementation in execution_order(pair):
                            name = f"pair{pair + 1:02d}-{implementation}"
                            print(name, flush=True)
                            report = (
                                current.run(
                                    current_images[head]["benchmark_id"],
                                    name,
                                    archive,
                                )
                                if implementation == "current"
                                else candidates.run(
                                    implementation, name, recovery=False
                                )
                            )
                            reports[implementation].append(report)
                        summary = summarize_completed_boundary(reports)
                        if summary is not None:
                            write_report(summary, evidence / "comparison.json")

                    recovery_archive = {**archive, "queue_recovery": True}
                    recovery = {
                        "current": current.run(
                            current_images[head]["benchmark_id"],
                            "recovery-current",
                            recovery_archive,
                        ),
                        "apalis": candidates.run(
                            "apalis", "recovery-apalis", recovery=True
                        ),
                        "azums": candidates.run(
                            "azums", "recovery-azums", recovery=True
                        ),
                    }
                    (evidence / "recovery.json").write_text(
                        json.dumps(recovery, indent=2) + "\n"
                    )
        finally:
            for path in evidence.rglob("*"):
                if path.is_file() and path.suffix in {".json", ".log", ".md"}:
                    path.write_text(
                        path.read_text(errors="replace").replace(
                            password, "[redacted]"
                        )
                    )


if __name__ == "__main__":
    main()
