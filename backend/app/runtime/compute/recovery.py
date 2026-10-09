"""One allocation recovery attempt; infrastructure faults retain their identity."""

import json
import time

from app.modules.inference.worker_protocol import WorkerRequest, WorkerResult

from .contracts import ComputeUnavailable, Reason


def allocation_failure(exc: Exception) -> bool:
    return (
        isinstance(exc, MemoryError)
        or type(exc).__name__ == "GPUOutOfMemoryError"
        or (
            type(exc).__module__.startswith("onnxruntime")
            and any(
                marker in str(exc).casefold()
                for marker in (
                    "out of memory",
                    "failed to allocate memory",
                    "resource_exhausted",
                )
            )
        )
    )


def execute(worker, payload: bytes, deadline: float, evict) -> bytes:
    try:
        return worker.execute(payload)
    except Exception as exc:
        if not allocation_failure(exc):
            raise
    message = WorkerRequest.model_validate_json(payload)
    items = message.sparse_texts if message.sparse_texts else message.inputs
    if len(items) < 2:
        raise ComputeUnavailable(Reason.CAPACITY)
    evict()
    midpoint = len(items) // 2
    results = []
    for part in (items[:midpoint], items[midpoint:]):
        if time.monotonic() >= deadline:
            raise ComputeUnavailable(Reason.DEADLINE)
        update = {"sparse_texts" if message.sparse_texts else "inputs": part}
        try:
            results.append(
                worker.execute(
                    message.model_copy(update=update).model_dump_json().encode()
                )
            )
        except Exception as exc:
            if allocation_failure(exc):
                raise ComputeUnavailable(Reason.CAPACITY) from exc
            raise
    if message.sparse_texts:
        return json.dumps(
            {
                "sparse_results": [
                    item
                    for result in results
                    for item in json.loads(result)["sparse_results"]
                ]
            },
            allow_nan=False,
        ).encode()
    pieces = [WorkerResult.model_validate_json(result) for result in results]
    if any(piece.config_hash != message.config_hash for piece in pieces):
        raise ValueError("compute_batch_identity_mismatch")
    return (
        WorkerResult(
            config_hash=message.config_hash,
            vectors=[vector for piece in pieces for vector in piece.vectors],
            truncated=[value for piece in pieces for value in piece.truncated],
        )
        .model_dump_json()
        .encode()
    )
