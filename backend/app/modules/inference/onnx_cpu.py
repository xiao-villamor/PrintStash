"""CPU-only ONNX sessions; loaded exclusively in the monitored inference child."""

from __future__ import annotations

import hashlib
from pathlib import Path

from printstash_core.inference import EmbeddingError, EmbeddingInput, EmbeddingSpace
from printstash_core.inference.vectors import normalize

from app.modules.inference.manifest import LocalModelManifest, ModelAsset, verify_assets


def _verified_bytes(directory: Path, asset: ModelAsset) -> bytes:
    payload = (directory / asset.filename).read_bytes()
    if hashlib.sha256(payload).hexdigest() != asset.sha256:
        raise EmbeddingError("embedding_asset_digest_mismatch")
    return payload


class OnnxCpuProvider:
    def __init__(self, directory: Path, manifest: LocalModelManifest, threads: int):
        if type(threads) is not int or not 1 <= threads <= 4:
            raise EmbeddingError("embedding_thread_budget_invalid")
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise EmbeddingError("embedding_runtime_unavailable") from exc
        verify_assets(directory, manifest)
        self.manifest = manifest
        self.space = manifest.space()
        self.tokenizer = None
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        self.image = ort.InferenceSession(
            _verified_bytes(directory, manifest.image.graph),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.image.disable_fallback()
        image_size = manifest.image.image_size
        self._validate_signature(
            self.image,
            {
                manifest.image.input_name: (
                    "tensor(float)",
                    (1, 3, image_size, image_size),
                )
            },
            manifest.image.output_name,
        )
        self.text = None
        if manifest.text is not None:
            try:
                from tokenizers import Tokenizer
            except ImportError as exc:
                raise EmbeddingError("embedding_runtime_unavailable") from exc
            self.tokenizer = Tokenizer.from_str(
                _verified_bytes(directory, manifest.text.tokenizer).decode("utf-8")
            )
            self.tokenizer.enable_truncation(max_length=manifest.text.max_tokens)
            self.tokenizer.enable_padding(
                length=manifest.text.max_tokens,
                pad_id=manifest.text.pad_id,
                pad_token=manifest.text.pad_token,
            )
            self.text = ort.InferenceSession(
                _verified_bytes(directory, manifest.text.graph),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            self.text.disable_fallback()
            inputs = {
                manifest.text.input_name: (
                    "tensor(int64)",
                    (1, manifest.text.max_tokens),
                )
            }
            if manifest.text.attention_mask_name is not None:
                inputs[manifest.text.attention_mask_name] = (
                    "tensor(int64)",
                    (1, manifest.text.max_tokens),
                )
            self._validate_signature(self.text, inputs, manifest.text.output_name)
        self._check_canaries()

    def _validate_signature(
        self,
        session,
        expected_inputs: dict[str, tuple[str, tuple[int, ...]]],
        output_name: str,
    ) -> None:
        actual = {node.name: node for node in session.get_inputs()}
        if set(actual) != set(expected_inputs):
            raise EmbeddingError("embedding_signature_mismatch")
        for name, (dtype, dimensions) in expected_inputs.items():
            node = actual[name]
            if (
                node.type != dtype
                or len(node.shape) != len(dimensions)
                or any(
                    isinstance(size, int) and size != expected
                    for size, expected in zip(node.shape, dimensions, strict=True)
                )
            ):
                raise EmbeddingError("embedding_signature_mismatch")
        outputs = {node.name: node for node in session.get_outputs()}
        output = outputs.get(output_name)
        if (
            output is None
            or output.type != "tensor(float)"
            or len(output.shape) != 2
            or any(
                isinstance(size, int) and size != expected
                for size, expected in zip(
                    output.shape, (1, self.space.dimension), strict=True
                )
            )
        ):
            raise EmbeddingError("embedding_signature_mismatch")

    def _one(self, item: EmbeddingInput) -> tuple[float, ...]:
        import numpy as np

        if item.modality == "text":
            if (
                self.text is None
                or self.tokenizer is None
                or self.manifest.text is None
            ):
                raise EmbeddingError("embedding_text_unavailable")
            contract = self.manifest.text
            tokens = self.tokenizer.encode(self.space.query_prefix + (item.text or ""))
            values = {contract.input_name: np.asarray([tokens.ids], dtype=np.int64)}
            if contract.attention_mask_name is not None:
                values[contract.attention_mask_name] = np.asarray(
                    [tokens.attention_mask], dtype=np.int64
                )
            result = self.text.run([contract.output_name], values)[0]
        else:
            from PIL import Image

            image_contract = self.manifest.image
            image = Image.frombytes("RGB", (item.width, item.height), item.rgb or b"")
            image = image.resize(
                (image_contract.image_size, image_contract.image_size),
                Image.Resampling.BICUBIC,
            )
            pixels = np.asarray(image, dtype=np.float32) / 255
            pixels = (
                pixels - np.asarray(image_contract.mean, dtype=np.float32)
            ) / np.asarray(image_contract.std, dtype=np.float32)
            tensor = np.ascontiguousarray(
                pixels.transpose(2, 0, 1)[None], dtype=np.float32
            )
            result = self.image.run(
                [image_contract.output_name], {image_contract.input_name: tensor}
            )[0]
        if result.shape != (1, self.space.dimension) or result.dtype != np.float32:
            raise EmbeddingError("embedding_output_mismatch")
        return tuple(
            float(value)
            for value in np.frombuffer(
                normalize(result[0], self.space.dimension), dtype="<f4"
            )
        )

    def _check_canaries(self) -> None:
        import numpy as np

        canary = EmbeddingInput("image", rgb=bytes([127, 127, 127]), width=1, height=1)
        cases = [(canary, self.manifest.image.canary)]
        if self.manifest.text is not None:
            cases.append(
                (
                    EmbeddingInput("text", text=self.manifest.text.canary_text),
                    self.manifest.text.canary,
                )
            )
        for item, expected in cases:
            actual = self._one(item)
            if abs(float(np.linalg.norm(expected)) - 1) > 0.001 or not np.allclose(
                actual, expected, atol=self.manifest.canary_tolerance, rtol=0
            ):
                raise EmbeddingError("embedding_canary_mismatch")

    def embed(
        self, inputs: tuple[EmbeddingInput, ...], space: EmbeddingSpace
    ) -> tuple[tuple[float, ...], ...]:
        if space != self.space:
            raise EmbeddingError("embedding_space_mismatch")
        if not 1 <= len(inputs) <= 8:
            raise EmbeddingError("embedding_batch_budget")
        return tuple(self._one(item) for item in inputs)
