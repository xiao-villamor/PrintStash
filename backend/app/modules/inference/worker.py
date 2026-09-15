"""Bounded pipe protocol for one monitored native inference operation."""

from __future__ import annotations

import base64
import json
import struct
import sys
from pathlib import Path
from typing import BinaryIO

from printstash_core.inference import EmbeddingError, EmbeddingInput, EmbeddingSpace

from app.modules.inference.manifest import (
    SparseModelManifest,
    manifest_identity,
    read_manifest,
    validate_space,
)
from app.modules.inference.worker_protocol import (
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    WorkerRequest,
)


class NativeWorker:
    def __init__(self, modeldir: Path, model_key: str, threads: int):
        self.manifest = read_manifest(modeldir, model_key)
        self.directory, self.threads = modeldir, threads
        self.provider = None

    def execute(self, payload: bytes) -> bytes:
        from app.modules.inference.onnx_cpu import OnnxCpuProvider

        if len(payload) > MAX_INPUT_BYTES:
            raise EmbeddingError("embedding_input_budget")
        request = WorkerRequest.model_validate_json(payload)
        if isinstance(self.manifest, SparseModelManifest):
            from app.modules.inference.sparse import SparseNativeProvider

            if (
                request.config_hash != manifest_identity(self.manifest)
                or request.inputs
                or request.space_json
            ):
                raise EmbeddingError("embedding_space_mismatch")
            if self.provider is None:
                self.provider = SparseNativeProvider(
                    self.directory, self.manifest, self.threads
                )
            return self.provider.expand(request.sparse_text).model_dump_json().encode()
        if request.sparse_text is not None:
            raise EmbeddingError("embedding_sparse_required")
        space = (
            EmbeddingSpace(**json.loads(request.space_json))
            if request.space_json
            else self.manifest.space()
        )
        validate_space(self.manifest, space)
        if request.config_hash != space.config_hash:
            raise EmbeddingError("embedding_space_mismatch")
        inputs = []
        for item in request.inputs:
            try:
                rgb = (
                    base64.b64decode(item.rgb_base64, validate=True)
                    if item.rgb_base64 is not None
                    else None
                )
                points = (
                    base64.b64decode(item.points_base64, validate=True)
                    if item.points_base64 is not None
                    else None
                )
            except ValueError:
                raise EmbeddingError("embedding_input_invalid") from None
            inputs.append(
                EmbeddingInput(
                    item.modality,
                    text=item.text,
                    rgb=rgb,
                    width=item.width,
                    height=item.height,
                    points=points,
                )
            )
        if self.provider is None:
            self.provider = OnnxCpuProvider(self.directory, self.manifest, self.threads)
        vectors = (
            self.provider.embed(tuple(inputs), self.provider.space) if inputs else ()
        )
        result = json.dumps(
            {
                "vectors": vectors,
                "config_hash": space.config_hash,
                "truncated": self.provider.truncations if inputs else [],
            },
            allow_nan=False,
        ).encode()
        if len(result) > MAX_OUTPUT_BYTES:
            raise EmbeddingError("embedding_output_budget")
        return result


def execute(payload: bytes, modeldir: Path, model_key: str, threads: int) -> bytes:
    return NativeWorker(modeldir, model_key, threads).execute(payload)


def serve(source: BinaryIO, destination: BinaryIO) -> int:
    worker = None
    while header := source.read(4):
        if len(header) != 4:
            return 2
        length = struct.unpack("!I", header)[0]
        if length > MAX_INPUT_BYTES:
            return 2
        payload = source.read(length)
        if len(payload) != length:
            return 2
        try:
            if worker is None:
                worker = NativeWorker(Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]))
            result = worker.execute(payload)
        except EmbeddingError as exc:
            result = json.dumps({"code": exc.code}).encode()
        except (Exception, MemoryError):
            result = b'{"code":"embedding_inference_failed"}'
        destination.write(struct.pack("!I", len(result)) + result)
        destination.flush()
        del payload, result
    return 0


def main(
    input_stream: BinaryIO | None = None, output_stream: BinaryIO | None = None
) -> int:
    if len(sys.argv) != 4:
        return 2
    source = input_stream or sys.stdin.buffer
    destination = output_stream or sys.stdout.buffer
    try:
        result = execute(
            source.read(MAX_INPUT_BYTES + 1),
            Path(sys.argv[1]),
            sys.argv[2],
            int(sys.argv[3]),
        )
        status = 0
    except EmbeddingError as exc:
        result, status = json.dumps({"code": exc.code}).encode(), 3
    except (Exception, MemoryError):
        # Neither native exceptions nor query text cross the process boundary.
        result, status = b'{"code":"embedding_inference_failed"}', 4
    destination.write(result)
    destination.flush()
    return status


if __name__ == "__main__":
    # Preserve a private protocol descriptor before routing native stdout to
    # stderr. ONNX diagnostics cannot corrupt or grow the response pipe.
    import os

    with os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0) as protocol:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
        raise SystemExit(
            serve(sys.stdin.buffer, protocol)
            if len(sys.argv) == 5 and sys.argv[4] == "--persistent"
            else main(output_stream=protocol)
        )
