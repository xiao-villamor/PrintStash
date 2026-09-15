"""A deterministic protocol fake for executor/cache tests; no semantic quality claim."""

from dataclasses import dataclass, field
from threading import Event

from printstash_core.inference import EmbeddingInput, EmbeddingSpace
from printstash_core.inference.context import InferenceContext


@dataclass
class RecordingEmbeddingProvider:
    calls: list[tuple[EmbeddingInput, ...]] = field(default_factory=list)
    started: Event = field(default_factory=Event)
    release: Event | None = None
    error: Exception | None = None
    vectors: tuple[tuple[float, ...], ...] | None = None

    def embed(
        self,
        inputs: tuple[EmbeddingInput, ...],
        space: EmbeddingSpace,
        *,
        context: InferenceContext,
    ):
        self.calls.append(inputs)
        self.started.set()
        if self.release is not None:
            self.release.wait(5)
        context.remaining()
        if self.error is not None:
            raise self.error
        if self.vectors is not None:
            return self.vectors
        return tuple((1.0,) + (0.0,) * (space.dimension - 1) for _ in inputs)
