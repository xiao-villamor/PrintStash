"""Versioned derived vector codes; the native float32 vector remains authoritative."""

from __future__ import annotations

import heapq
import json
from dataclasses import asdict, dataclass
from itertools import islice
from typing import TYPE_CHECKING, Any, Iterable, Literal, cast

if TYPE_CHECKING:
    from numpy.typing import NDArray

from .embedding import EmbeddingError
from .vectors import normalize


@dataclass(frozen=True)
class IndexTransform:
    native_dimension: int
    index_dimension: int
    quantization: Literal["float32", "int8", "binary"] = "float32"
    version: str = "unit-prefix-v1"
    int8_scale: float = 1 / 127
    bit_order: Literal["little"] = "little"

    def __post_init__(self):
        if (
            type(self.native_dimension) is not int
            or type(self.index_dimension) is not int
            or not 1 <= self.index_dimension <= self.native_dimension <= 4096
            or self.quantization not in {"float32", "int8", "binary"}
            or self.version != "unit-prefix-v1"
            or self.int8_scale != 1 / 127
            or self.bit_order != "little"
        ):
            raise EmbeddingError("embedding_transform_invalid")

    @classmethod
    def approved(
        cls,
        native_dimension: int,
        index_dimension: int,
        quantization: str,
        *,
        mrl_dimensions: tuple[int, ...] = (),
    ):
        if (
            index_dimension != native_dimension
            and index_dimension not in mrl_dimensions
        ):
            raise EmbeddingError("embedding_mrl_unavailable")
        return cls(
            native_dimension,
            index_dimension,
            cast(Literal["float32", "int8", "binary"], quantization),
        )

    def metadata(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def restore(
        cls,
        value: object,
        *,
        native_dimension: int,
        index_dimension: int,
        quantization: str,
    ):
        try:
            if not isinstance(value, str) or len(value) > 4096:
                raise ValueError()
            data = json.loads(value)
            # Legacy full-float generations predate versioned transforms.
            recipe = (
                cls(native_dimension, index_dimension, quantization)
                if data == {}
                and native_dimension == index_dimension
                and quantization == "float32"
                else cls(**data)
            )
            if (
                recipe.native_dimension,
                recipe.index_dimension,
                recipe.quantization,
            ) != (native_dimension, index_dimension, quantization):
                raise ValueError()
            return recipe
        except (ValueError, TypeError, RecursionError):
            raise EmbeddingError("embedding_transform_invalid") from None

    @property
    def code_bytes(self) -> int:
        if self.quantization == "binary":
            return (self.index_dimension + 7) // 8
        return self.index_dimension * (4 if self.quantization == "float32" else 1)

    @property
    def storage_dimension(self) -> int:
        return (
            self.code_bytes * 8
            if self.quantization == "binary"
            else self.index_dimension
        )

    def prefix(self, native: bytes) -> bytes:
        import numpy as np

        if len(native) != self.native_dimension * 4:
            raise EmbeddingError("embedding_dimension_mismatch")
        values = np.frombuffer(native, dtype="<f4")
        if not np.isfinite(values).all():
            raise EmbeddingError("embedding_vector_invalid")
        return normalize(values[: self.index_dimension], self.index_dimension)

    def encode(self, native: bytes) -> bytes:
        import numpy as np

        prefix = self.prefix(native)
        if self.quantization == "float32":
            return prefix
        values = np.frombuffer(prefix, dtype="<f4")
        if self.quantization == "int8":
            return (
                np.clip(np.rint(values / self.int8_scale), -127, 127)
                .astype("i1")
                .tobytes()
            )
        # Zero has no positive sign. Padding bits are always zero, including for
        # dimensions not divisible by eight; Hamming distance is unchanged.
        return np.packbits(values > 0, bitorder=self.bit_order).tobytes()

    def distances(self, query: bytes, codes: tuple[bytes, ...]) -> NDArray[Any]:
        """Bounded block distances for the portable compressed-index adapter."""
        import numpy as np

        if (
            not 1 <= len(codes) <= 512
            or len(query) != self.code_bytes
            or any(len(code) != self.code_bytes for code in codes)
        ):
            raise EmbeddingError("embedding_code_invalid")
        if self.quantization == "binary":
            matrix = np.stack([np.frombuffer(code, dtype="u1") for code in codes])
            differences = np.bitwise_xor(matrix, np.frombuffer(query, dtype="u1"))
            return np.unpackbits(differences, axis=1, bitorder=self.bit_order)[
                :, : self.index_dimension
            ].sum(axis=1)
        dtype = "<f4" if self.quantization == "float32" else "i1"
        matrix = np.stack([np.frombuffer(code, dtype=dtype) for code in codes]).astype(
            np.float32
        )
        q = np.frombuffer(query, dtype=dtype).astype(np.float32)
        with np.errstate(over="ignore", invalid="ignore"):
            norms = np.linalg.norm(matrix, axis=1)
            qnorm = np.linalg.norm(q)
        if (
            not np.isfinite(matrix).all()
            or not np.isfinite(q).all()
            or not np.isfinite(norms).all()
            or not np.isfinite(qnorm)
            or np.any(norms < 1e-12)
            or qnorm < 1e-12
        ):
            raise EmbeddingError("embedding_code_invalid")
        return 1 - np.clip((matrix / norms[:, None]) @ (q / qnorm), -1, 1)


@dataclass(frozen=True)
class CodeShortlist:
    ids: tuple[int, ...]
    scanned: int
    truncated: bool


def shortlist_codes(
    transform: IndexTransform,
    query: bytes,
    entries: Iterable[tuple[int, bytes]],
    *,
    limit: int,
    max_scan: int,
) -> CodeShortlist:
    if not 1 <= limit <= 2048 or not 1 <= max_scan <= 1_000_000:
        raise EmbeddingError("embedding_query_budget_invalid")
    iterator = iter(entries)
    best: list[tuple[float, int]] = []
    scanned = 0
    while scanned < max_scan:
        block = tuple(islice(iterator, min(256, max_scan - scanned)))
        if not block:
            break
        distances = transform.distances(query, tuple(code for _, code in block))
        for (unit_id, _), distance in zip(block, distances, strict=True):
            rank = (-float(distance), -unit_id)
            if len(best) < limit:
                heapq.heappush(best, rank)
            elif rank > best[0]:
                heapq.heapreplace(best, rank)
        scanned += len(block)
    return CodeShortlist(
        tuple(-unit_id for _, unit_id in sorted(best, reverse=True)),
        scanned,
        next(iterator, None) is not None,
    )
