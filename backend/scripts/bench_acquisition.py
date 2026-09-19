"""Measure bounded URL acquisition through the public import seam."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import statistics
import tempfile
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterator

import psutil
from sqlalchemy import event
from sqlalchemy.pool import NullPool
from sqlmodel import SQLModel, create_engine

from app.core import url_safety
from app.core.config import _overlay
from app.db.session import (
    SQLiteSessionFactory,
    _set_sqlite_pragmas,
    get_session_factory,
    override_session_factory,
)
from app.modules.ingestion.importer import download_to_staging

DEFAULT_ITERATIONS = 3


def _percentile(values: list[float], percentile: float) -> float:
    return sorted(values)[max(0, math.ceil(percentile * len(values)) - 1)]


def _latency(samples: list[float]) -> dict[str, float]:
    return {
        "p50_ms": statistics.median(samples),
        "p95_ms": _percentile(samples, 0.95),
        "max_ms": max(samples),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextmanager
def _server(payloads: dict[str, bytes]) -> Iterator[tuple[str, dict[str, int]]]:
    counters = {"requests": 0, "bytes": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802 - stdlib server API
            counters["requests"] += 1
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/large")
                self.end_headers()
                return
            body = payloads.get(self.path.removeprefix("/"))
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            filename = "small.stl" if self.path == "/small" else "large.3mf"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Content-Disposition", f'attachment; filename="{filename}"'
            )
            self.end_headers()
            self.wfile.write(body)
            counters["bytes"] += len(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", counters
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


async def _measure(
    base_url: str,
    payloads: dict[str, bytes],
    iterations: int,
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, object]]]:
    samples: dict[str, list[float]] = {name: [] for name in payloads}
    samples["redirect"] = []
    catalog: dict[str, dict[str, object]] = {}
    for _ in range(iterations):
        for name in ("small", "large", "redirect"):
            started = time.perf_counter_ns()
            staged, filename = await download_to_staging(f"{base_url}/{name}")
            samples[name].append((time.perf_counter_ns() - started) / 1_000_000)
            expected = payloads["large" if name == "redirect" else name]
            current = {
                "filename": filename,
                "size_bytes": staged.stat().st_size,
                "sha256": _sha256(staged),
            }
            expected_result = {
                "filename": "small.stl" if name == "small" else "large.3mf",
                "size_bytes": len(expected),
                "sha256": hashlib.sha256(expected).hexdigest(),
            }
            if current != expected_result:
                raise RuntimeError(f"acquisition output changed: {name}")
            if name in catalog and catalog[name] != current:
                raise RuntimeError(f"acquisition output changed within run: {name}")
            catalog[name] = current
            staged.unlink()
    return ({name: _latency(values) for name, values in samples.items()}, catalog)


def run(iterations: int, *, quick: bool = False) -> dict[str, object]:
    if iterations < 1:
        raise ValueError("Iterations must be positive")
    small = (b"solid acquisition\nfacet normal 0 0 1\nendsolid\n" * 128)[:4096]
    large_size = 256 * 1024 if quick else 8 * 1024 * 1024
    large = (b"3MF acquisition benchmark\n" * (large_size // 26 + 1))[:large_size]
    payloads = {"small": small, "large": large}
    process = psutil.Process()
    peak_rss = process.memory_info().rss
    cpu_started = time.process_time()
    elapsed_started = time.perf_counter()
    previous_overlay = dict(_overlay)
    previous_validator = url_safety.is_public_ip
    previous_factory = get_session_factory()
    with tempfile.TemporaryDirectory(prefix="printstash-acquisition-") as temporary:
        root = Path(temporary)
        engine = create_engine(
            f"sqlite:///{root / 'capacity.db'}",
            connect_args={"check_same_thread": False},
            poolclass=NullPool,
        )
        event.listen(engine, "connect", _set_sqlite_pragmas)
        SQLModel.metadata.create_all(engine)
        override_session_factory(SQLiteSessionFactory(engine))
        _overlay.update(
            {
                "staging_dir": root / "staging",
                "max_upload_mb": 16,
                "storage_min_free_bytes": 0,
                "storage_min_free_percent": 0.0,
                "url_import_max_redirects": 4,
            }
        )
        url_safety.is_public_ip = lambda _ip: True
        try:
            with _server(payloads) as (base_url, counters):
                latency, catalog = asyncio.run(_measure(base_url, payloads, iterations))
            peak_rss = max(peak_rss, process.memory_info().rss)
        finally:
            url_safety.is_public_ip = previous_validator
            _overlay.clear()
            _overlay.update(previous_overlay)
            override_session_factory(previous_factory)
            engine.dispose()
    elapsed = time.perf_counter() - elapsed_started
    expected_requests = iterations * 4
    expected_bytes = iterations * (len(small) + 2 * len(large))
    if counters != {"requests": expected_requests, "bytes": expected_bytes}:
        raise RuntimeError("acquisition request accounting changed")
    return {
        "measurement_protocol": "native-acquisition-v1",
        "iterations_per_workload": iterations,
        "total_seconds": elapsed,
        "process_cpu_seconds": time.process_time() - cpu_started,
        "process_peak_rss_bytes": peak_rss,
        "throughput_bytes_per_second": expected_bytes / elapsed,
        "download_latency": latency,
        "network_counters": counters,
        "correctness_catalog": catalog,
    }


def _assert_compatible(reference: dict, report: dict) -> None:
    for key in ("measurement_protocol", "network_counters", "correctness_catalog"):
        if reference.get(key) != report.get(key):
            raise ValueError(f"acquisition benchmark correctness differs: {key}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--database", choices=("sqlite", "postgres"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()
    report = run(args.iterations, quick=args.quick)
    report["database_dialect"] = args.database
    if args.compare:
        _assert_compatible(json.loads(args.compare.read_text()), report)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
