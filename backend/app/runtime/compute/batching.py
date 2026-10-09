"""Coalesce only already-admitted compatible inference tickets, preserving order."""

import base64
import json
from dataclasses import dataclass

from app.modules.inference.worker_protocol import (
    MAX_INPUT_BYTES,
    WorkerRequest,
    WorkerResult,
)

from .protocol import InferenceRequest


@dataclass(frozen=True)
class Batch:
    request: InferenceRequest
    sizes: tuple[int, ...]
    config_hash: str
    sparse: bool = False

    def split(self, payload: bytes) -> tuple[bytes, ...]:
        if self.sparse:
            from app.modules.inference.sparse import SparseResult

            data = json.loads(payload)
            rows = [
                SparseResult.model_validate(value) for value in data["sparse_results"]
            ]
            if len(rows) != len(self.sizes) or any(
                row.config_hash != self.config_hash for row in rows
            ):
                raise ValueError("compute_batch_output_mismatch")
            return tuple(row.model_dump_json().encode() for row in rows)
        result = WorkerResult.model_validate_json(payload)
        if result.config_hash != self.config_hash:
            raise ValueError("compute_batch_identity_mismatch")
        if len(result.vectors) != sum(self.sizes) or len(result.truncated) != sum(
            self.sizes
        ):
            raise ValueError("compute_batch_output_mismatch")
        outputs = []
        offset = 0
        for size in self.sizes:
            outputs.append(
                result.model_copy(
                    update={
                        "vectors": result.vectors[offset : offset + size],
                        "truncated": result.truncated[offset : offset + size],
                    }
                )
                .model_dump_json()
                .encode()
            )
            offset += size
        return tuple(outputs)


def merge(requests: list[InferenceRequest]) -> Batch | None:
    if not requests:
        return None
    first = requests[0]
    messages = []
    for request in requests:
        if (
            request.directory,
            request.model_key,
            request.threads,
            request.priority,
        ) != (first.directory, first.model_key, first.threads, first.priority):
            return None
        raw = base64.b64decode(request.payload, validate=True)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("compute_input_budget")
        message = WorkerRequest.model_validate_json(raw)
        if message.sparse_texts or (not message.inputs and message.sparse_text is None):
            return None
        messages.append(message)
    reference = messages[0]
    if reference.sparse_text is not None:
        if len(messages) > 8 or any(
            message.sparse_text is None
            or message.inputs
            or message.space_json is not None
            or message.config_hash != reference.config_hash
            for message in messages
        ):
            return None
        payload = (
            reference.model_copy(
                update={
                    "sparse_text": None,
                    "sparse_texts": [message.sparse_text for message in messages],
                }
            )
            .model_dump_json()
            .encode()
        )
        return Batch(
            first.model_copy(
                update={
                    "deadline": max(request.deadline for request in requests),
                    "payload": base64.b64encode(payload).decode(),
                }
            ),
            tuple(1 for message in messages),
            reference.config_hash,
            True,
        )
    if any(
        not message.inputs or message.sparse_text is not None for message in messages
    ):
        return None
    modality = reference.inputs[0].modality
    if any(
        message.config_hash != reference.config_hash
        or message.space_json != reference.space_json
        or any(item.modality != modality for item in message.inputs)
        for message in messages
    ):
        return None
    count = sum(len(message.inputs) for message in messages)
    if count > 8:
        return None
    payload = (
        reference.model_copy(
            update={"inputs": [item for message in messages for item in message.inputs]}
        )
        .model_dump_json()
        .encode()
    )
    if len(payload) > MAX_INPUT_BYTES:
        return None
    return Batch(
        first.model_copy(
            update={
                "deadline": max(request.deadline for request in requests),
                "payload": base64.b64encode(payload).decode(),
            }
        ),
        tuple(len(message.inputs) for message in messages),
        reference.config_hash,
    )
