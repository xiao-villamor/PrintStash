"""CPU-only ONNX sessions; loaded exclusively in the monitored inference child."""

from __future__ import annotations

import hashlib
from pathlib import Path

from printstash_core.inference import EmbeddingError, EmbeddingInput, EmbeddingSpace
from printstash_core.inference.vectors import normalize

from app.modules.inference.manifest import (
    LocalModelManifest,
    ModelAsset,
    ModelManifest,
    PointModelManifest,
    TextModelManifest,
    verify_assets,
)


def _verified_bytes(directory: Path, asset: ModelAsset) -> bytes:
    payload = (directory / asset.filename).read_bytes()
    if hashlib.sha256(payload).hexdigest() != asset.sha256:
        raise EmbeddingError("embedding_asset_digest_mismatch")
    return payload


class OnnxCpuProvider:
    def __init__(self, directory: Path, manifest: ModelManifest, threads: int):
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
        self.count_tokenizer = None
        self.dynamic_text_length = False
        self.truncations: list[bool] = []
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        self.image = None
        if isinstance(manifest, (LocalModelManifest, PointModelManifest)):
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
            self.count_tokenizer = Tokenizer.from_str(self.tokenizer.to_str())
            self.count_tokenizer.no_truncation()
            self.count_tokenizer.no_padding()
            self.tokenizer.enable_truncation(max_length=manifest.text.max_tokens)
            self.tokenizer.enable_padding(
                length=manifest.text.max_tokens,
                pad_id=manifest.text.pad_id,
                pad_token=manifest.text.pad_token,
            )
            graph = _verified_bytes(directory, manifest.text.graph)
            if isinstance(manifest, TextModelManifest):
                self._verify_text_graph(graph, manifest.text.opset)
            self.text = ort.InferenceSession(
                graph,
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
            if (
                isinstance(manifest, TextModelManifest)
                and manifest.text.token_type_ids_name is not None
            ):
                inputs[manifest.text.token_type_ids_name] = (
                    "tensor(int64)",
                    (1, manifest.text.max_tokens),
                )
            self._validate_signature(
                self.text,
                inputs,
                manifest.text.output_name,
                sequence_length=manifest.text.max_tokens
                if isinstance(manifest, TextModelManifest)
                else None,
            )
            if (
                isinstance(manifest, TextModelManifest)
                and all(
                    not isinstance(node.shape[1], int)
                    for node in self.text.get_inputs()
                )
                and not isinstance(self.text.get_outputs()[0].shape[1], int)
            ):
                # Dynamic exports need only their real tokens. Padding every
                # short query to 512 makes CPU self-attention needlessly costly.
                self.dynamic_text_length = True
                self.tokenizer.enable_padding(
                    length=None,
                    pad_id=manifest.text.pad_id,
                    pad_token=manifest.text.pad_token,
                )
        self.point = None
        if isinstance(manifest, PointModelManifest):
            graph = _verified_bytes(directory, manifest.point.graph)
            self._verify_text_graph(graph, manifest.point.opset)
            self.point = ort.InferenceSession(
                graph, sess_options=options, providers=["CPUExecutionProvider"]
            )
            self.point.disable_fallback()
            self._validate_signature(
                self.point,
                {
                    manifest.point.centers_name: ("tensor(float)", (1, 3, 64)),
                    manifest.point.grouped_name: ("tensor(float)", (1, 9, 256, 64)),
                },
                manifest.point.output_name,
            )
        self._check_canaries()

    @staticmethod
    def _verify_text_graph(payload: bytes, opset: int) -> None:
        try:
            import onnx
        except ImportError:
            raise EmbeddingError("embedding_runtime_unavailable") from None
        model = onnx.load_model_from_string(payload)
        imports = {entry.domain: entry.version for entry in model.opset_import}
        if (
            imports.get("", imports.get("ai.onnx")) != opset
            or set(imports) - {"", "ai.onnx", "ai.onnx.ml", "com.microsoft"}
            or model.functions
        ):
            raise EmbeddingError("embedding_opset_mismatch")
        graphs = [model.graph]
        nodes = 0
        while graphs:
            graph = graphs.pop()
            tensors = list(graph.initializer)
            for sparse in graph.sparse_initializer:
                tensors.extend((sparse.values, sparse.indices))
            for node in graph.node:
                nodes += 1
                if nodes > 100_000 or node.domain not in imports:
                    raise EmbeddingError("embedding_graph_budget")
                for attribute in node.attribute:
                    if attribute.type == onnx.AttributeProto.GRAPH:
                        graphs.append(attribute.g)
                    elif attribute.type == onnx.AttributeProto.GRAPHS:
                        graphs.extend(attribute.graphs)
                    elif attribute.type == onnx.AttributeProto.TENSOR:
                        tensors.append(attribute.t)
                    elif attribute.type == onnx.AttributeProto.TENSORS:
                        tensors.extend(attribute.tensors)
                    elif attribute.type == onnx.AttributeProto.SPARSE_TENSOR:
                        tensors.extend(
                            (
                                attribute.sparse_tensor.values,
                                attribute.sparse_tensor.indices,
                            )
                        )
                    elif attribute.type == onnx.AttributeProto.SPARSE_TENSORS:
                        for sparse in attribute.sparse_tensors:
                            tensors.extend((sparse.values, sparse.indices))
            if any(
                tensor.data_location == onnx.TensorProto.EXTERNAL
                or tensor.external_data
                for tensor in tensors
            ):
                raise EmbeddingError("embedding_external_data_forbidden")

    def _validate_signature(
        self,
        session,
        expected_inputs: dict[str, tuple[str, tuple[int, ...]]],
        output_name: str,
        sequence_length: int | None = None,
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
        expected_shape = (
            (1, sequence_length, self.space.dimension)
            if sequence_length is not None
            else (1, self.space.dimension)
        )
        if (
            output is None
            or output.type != "tensor(float)"
            or len(output.shape) != len(expected_shape)
            or any(
                isinstance(size, int) and size != expected
                for size, expected in zip(output.shape, expected_shape, strict=True)
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
            text = item.text or ""
            if not isinstance(self.manifest, TextModelManifest):
                text = self.space.query_prefix + text
            tokens = self.tokenizer.encode(text)
            self.truncations.append(
                len(self.count_tokenizer.encode(text).ids) > contract.max_tokens
            )
            values = {contract.input_name: np.asarray([tokens.ids], dtype=np.int64)}
            if contract.attention_mask_name is not None:
                values[contract.attention_mask_name] = np.asarray(
                    [tokens.attention_mask], dtype=np.int64
                )
            if (
                isinstance(self.manifest, TextModelManifest)
                and self.manifest.text.token_type_ids_name is not None
            ):
                values[self.manifest.text.token_type_ids_name] = np.asarray(
                    [tokens.type_ids], dtype=np.int64
                )
            result = self.text.run([contract.output_name], values)[0]
            if isinstance(self.manifest, TextModelManifest):
                if result.shape != (
                    1,
                    len(tokens.ids)
                    if self.dynamic_text_length
                    else self.manifest.text.max_tokens,
                    self.space.dimension,
                ):
                    raise EmbeddingError("embedding_output_mismatch")
                if self.manifest.text.pooling == "cls":
                    result = result[:, 0, :]
                else:
                    mask = np.asarray(tokens.attention_mask, dtype=np.float32)[
                        None, :, None
                    ]
                    result = (result * mask).sum(axis=1) / max(float(mask.sum()), 1)
        elif item.modality == "point_cloud":
            from printstash_core.inference.points import grouped_points

            if not isinstance(self.manifest, PointModelManifest) or self.point is None:
                raise EmbeddingError("embedding_point_unavailable")
            centers, grouped = grouped_points(item)
            point = self.manifest.point
            self.truncations.append(False)
            result = self.point.run(
                [point.output_name],
                {point.centers_name: centers, point.grouped_name: grouped},
            )[0]
        else:
            from PIL import Image

            if (
                not isinstance(self.manifest, (LocalModelManifest, PointModelManifest))
                or self.image is None
            ):
                raise EmbeddingError("embedding_image_unavailable")
            self.truncations.append(False)
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
        cases = (
            [(canary, self.manifest.image.canary)]
            if isinstance(self.manifest, (LocalModelManifest, PointModelManifest))
            else []
        )
        if isinstance(self.manifest, PointModelManifest):
            from printstash_core.inference.points import canary_input

            cases.append((canary_input(), self.manifest.point.canary))
        if self.manifest.text is not None:
            cases.append(
                (
                    EmbeddingInput(
                        "text",
                        text=(
                            self.manifest.query_prefix
                            if isinstance(self.manifest, TextModelManifest)
                            else ""
                        )
                        + self.manifest.text.canary_text,
                    ),
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
        self.truncations = []
        return tuple(self._one(item) for item in inputs)
