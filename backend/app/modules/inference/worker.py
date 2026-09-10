"""Bounded file protocol for one monitored native inference operation."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Literal

from printstash_core.inference import EmbeddingError, EmbeddingInput
from pydantic import BaseModel, ConfigDict, Field

from app.modules.inference.manifest import read_manifest


class WorkerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    modality: Literal["text", "image"]
    text: str | None = Field(default=None, max_length=4096)
    width: int = Field(default=0, ge=0, le=1024, strict=True)
    height: int = Field(default=0, ge=0, le=1024, strict=True)


class WorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    inputs: list[WorkerInput] = Field(default_factory=list, max_length=8)


def execute(workdir: Path, modeldir: Path, model_key: str, threads: int) -> None:
    from app.modules.inference.onnx_cpu import OnnxCpuProvider

    request_path = workdir / "request.json"
    if request_path.stat().st_size > 64 * 1024:
        raise EmbeddingError("embedding_input_budget")
    request = WorkerRequest.model_validate_json(request_path.read_bytes())
    manifest = read_manifest(modeldir, model_key)
    space = manifest.space()
    if request.config_hash != space.config_hash:
        raise EmbeddingError("embedding_space_mismatch")
    inputs = []
    for index, item in enumerate(request.inputs):
        rgb = None
        if item.modality == "image":
            path = workdir / f"{index}.rgb"
            if path.is_symlink() or path.stat().st_size != item.width * item.height * 3:
                raise EmbeddingError("embedding_input_invalid")
            rgb = path.read_bytes()
        inputs.append(
            EmbeddingInput(
                item.modality,
                text=item.text,
                rgb=rgb,
                width=item.width,
                height=item.height,
            )
        )
    provider = OnnxCpuProvider(modeldir, manifest, threads)
    vectors = provider.embed(tuple(inputs), space) if inputs else ()
    (workdir / "result.json").write_text(
        json.dumps({"vectors": vectors, "config_hash": space.config_hash})
    )


def main() -> int:
    if len(sys.argv) != 5:
        return 2
    workdir = Path(sys.argv[1])
    try:
        execute(workdir, Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]))
        return 0
    except EmbeddingError as exc:
        (workdir / "error.json").write_text(json.dumps({"code": exc.code}))
        return 3
    except (Exception, MemoryError):
        # Neither native exception strings nor paths cross the process boundary.
        (workdir / "error.json").write_text(
            json.dumps({"code": "embedding_inference_failed"})
        )
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
