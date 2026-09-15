"""Bounded mesh computation ahead of the ordered ingestion writer.

Workers never access a Session or publish artifacts. Admission is acquired in
input order before submission and held until the writer consumes the result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any, Callable, Iterator, Protocol

from printstash_core.imports import StagedAsset

from app.core.logging import get_logger
from app.db.models import SUFFIX_TO_FILE_TYPE, FileType
from app.modules.media import render_budget


class Task(Protocol):
    def result(self, timeout: float | None = None) -> Any: ...


class Executor(Protocol):
    def submit(self, function: Callable[..., Any], *args: Any) -> Task: ...

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None: ...


StagedFile = tuple[Path, str] | StagedAsset
Progress = Callable[[float], None]
logger = get_logger(__name__)


@dataclass
class Analysis:
    value: tuple[dict[str, Any], bytes | None] | None = None
    error: str | None = None
    labels: list[str] = field(default_factory=list)
    lock: Lock = field(default_factory=Lock)

    def report(self, label: str) -> None:
        with self.lock:
            if label not in self.labels and len(self.labels) < 3:
                self.labels.append(label)

    def progress(self) -> float:
        with self.lock:
            return len(self.labels) * 20.0

    def process(self, _path: Path, report: Callable[[str], None]):
        for label in self.labels:
            report(label)
        if self.error is not None:
            raise RuntimeError(self.error)
        assert self.value is not None
        return self.value


def _compute(path: Path, file_type: FileType, analysis: Analysis) -> None:
    from app.modules.ingestion.ingestion import _mesh_strategy

    token = render_budget.ADAPTIVE_RENDER.set(True)
    try:
        analysis.value = _mesh_strategy(file_type, defer_fingerprint=True).process(
            path, analysis.report
        )
    except Exception as exc:  # noqa: BLE001 - reported through the normal file boundary
        # A Future exception retains its traceback and potentially an entire mesh.
        analysis.error = str(exc) or type(exc).__name__
    finally:
        render_budget.ADAPTIVE_RENDER.reset(token)


class PreparedImports:
    def __init__(
        self,
        files: list[StagedFile],
        progress: Progress,
        current_item: Callable[[str], None] | None = None,
    ):
        self.files = files
        self.progress = progress
        self.current_item = current_item
        self.workers = render_budget.import_workers()
        self.capacity = render_budget.memory_budget()
        logger.info(
            "mesh_import_admission workers=%d memory_budget_bytes=%d",
            self.workers,
            self.capacity,
        )
        from printstash_core.mesh.native_rasterizer import kernel

        self.executor: Executor = kernel().NativeExecutor(self.workers)
        self.pending: dict[int, tuple[Task, Analysis, render_budget.Reservation]] = {}
        self.next_submit = 0

    def __enter__(self) -> PreparedImports:
        return self

    def __exit__(self, *_exc) -> None:
        # Never remove a staged input while an outstanding worker can read it.
        self.executor.shutdown(wait=True, cancel_futures=True)
        for _, _, reservation in self.pending.values():
            reservation.release()
        self.pending.clear()

    def _fill(self, current: int) -> None:
        if self.workers == 1:
            return
        while self.next_submit < min(len(self.files), current + self.workers):
            index = self.next_submit
            item = self.files[index]
            path, name = (
                (item.staged_path, item.resolved.source_filename)
                if isinstance(item, StagedAsset)
                else item
            )
            file_type = SUFFIX_TO_FILE_TYPE.get(Path(name).suffix.lower())
            if file_type is None or file_type == FileType.GCODE:
                self.next_submit += 1
                continue
            try:
                size = render_budget.estimate_work(path, file_type.value, self.capacity)
            except (OSError, ValueError):
                size = self.capacity
            reservation = render_budget.budget.acquire(
                size,
                capacity=self.capacity,
                jobs=max(self.workers, int(render_budget.settings.max_render_jobs), 1),
                wait=index == current,
            )
            if reservation is None:
                return
            analysis = Analysis()
            try:
                future = self.executor.submit(_compute, path, file_type, analysis)
            except BaseException:
                reservation.release()
                raise
            self.pending[index] = future, analysis, reservation
            self.next_submit += 1

    def __iter__(self) -> Iterator[tuple[StagedFile, Analysis | None]]:
        for index, item in enumerate(self.files):
            if self.current_item is not None:
                name = (
                    item.resolved.source_filename
                    if isinstance(item, StagedAsset)
                    else item[1]
                )
                self.current_item(name)
            self._fill(index)
            entry = self.pending.get(index)
            analysis = None
            if entry is not None:
                future, analysis, _reservation = entry
                last = -1.0
                last_report = float("-inf")
                while True:
                    progress = analysis.progress()
                    now = monotonic()
                    if progress != last or now - last_report >= 1.0:
                        self.progress(progress)
                        last = progress
                        last_report = now
                    try:
                        future.result(timeout=0.1)
                        break
                    except TimeoutError:
                        continue
            try:
                yield item, analysis
            finally:
                entry = self.pending.pop(index, None)
                if entry is not None:
                    entry[2].release()
