"""Bound materialized mesh sources while keeping I/O separate from native work."""

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.core.cancellation import checkpoint
from app.core.config import settings
from app.modules.storage.artifact_content import ArtifactHandle
from app.runtime import preparation_runtime
from app.runtime.native_admission import NativePermit, Resources


def capacity() -> Resources:
    jobs = settings.mesh_prepared_max_jobs or max(2, settings.max_render_jobs + 1)
    return Resources(jobs, settings.mesh_prepared_max_mb * 1024**2)


@dataclass
class SourceBatch:
    _handles: tuple[ArtifactHandle, ...]
    _permit: NativePermit
    _materializing: bool = False

    @property
    def directory(self) -> Path:
        return preparation_runtime.workspace(self._permit)

    @contextmanager
    def materialize(
        self, *, capacity_claimed: bool = False
    ) -> Iterator[tuple[Path, ...]]:
        # A retained batch object cannot outlive its reservation or create a
        # second set of copies while the first set is still in use.
        _ = self._permit.fileno
        if self._materializing:
            raise RuntimeError("source batch already materialized")
        self._materializing = True
        try:
            with ExitStack() as cleanup:
                paths = []
                for handle in self._handles:
                    with preparation_runtime.io_slot(
                        settings.mesh_source_io_jobs, checkpoint=checkpoint
                    ):
                        paths.append(
                            cleanup.enter_context(
                                handle.materialize(
                                    capacity_claimed=capacity_claimed,
                                    directory=self.directory,
                                )
                            )
                        )
                yield tuple(paths)
        finally:
            self._materializing = False


@contextmanager
def reserve_sources(
    handles: tuple[ArtifactHandle, ...], *, output_bytes: int = 0
) -> Iterator[SourceBatch]:
    """Admit bytes before copying or taking disk leases; preserve them until close.

    Two copies per source cover remote external-source staging followed by its
    verified copy. Local/cache hits consume the same conservative allowance.
    ``output_bytes`` adds a bounded expanded output to the same lifetime.
    Disk free-space leases remain the storage owner's separate responsibility.
    """
    if type(output_bytes) is not int or output_bytes < 0:
        raise ValueError("output bytes must be a nonnegative integer")
    if not handles:
        raise ValueError("source preparation requires at least one Artifact")
    sizes = tuple(handle.file.size_bytes for handle in handles)
    if any(type(size) is not int or size < 0 for size in sizes):
        raise ValueError("source preparation requires known nonnegative sizes")
    request = Resources(1, max(1, 2 * sum(sizes) + output_bytes))
    with preparation_runtime.reserve(
        request, capacity(), checkpoint=checkpoint
    ) as permit:
        yield SourceBatch(handles, permit)


@contextmanager
def prepare_sources(handles: tuple[ArtifactHandle, ...]) -> Iterator[tuple[Path, ...]]:
    """Common path whose materializers own their existing disk capacity claims."""
    with reserve_sources(handles) as batch, batch.materialize() as paths:
        yield paths
