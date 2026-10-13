"""Bounded, document-only SPLADE inference in the shared native worker."""

import json
import re
from pathlib import Path

from printstash_core.inference import EmbeddingError, InferenceContext
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.manifest import (
    SparseModelManifest,
    SparseTerm,
    manifest_identity,
    read_manifest,
    verify_assets,
)
from app.modules.inference.model_cache import safe_directory
from app.modules.inference.worker_protocol import WorkerError


class SparseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config_hash: str
    terms: list[SparseTerm] = Field(max_length=128)
    truncated: bool


class LocalSparseProvider(LocalEmbeddingProvider):
    def __init__(self, sessions, directory: Path, model_key: str, threads: int):
        if type(threads) is not int or not 1 <= threads <= 4:
            raise EmbeddingError("embedding_thread_budget_invalid")
        self.sessions = sessions
        self.directory = safe_directory(directory)
        self.model_key, self.threads = model_key, threads
        manifest = read_manifest(self.directory, model_key)
        if not isinstance(manifest, SparseModelManifest):
            raise EmbeddingError("embedding_sparse_required")
        self.manifest = manifest

    def validate(self, *, context: InferenceContext | None = None):
        self.expand(None, context=context)
        return self.manifest

    def expand(
        self, text: str | None, *, context: InferenceContext | None = None
    ) -> SparseResult:
        if text is not None and (
            not isinstance(text, str) or not 0 < len(text) <= 16384
        ):
            raise EmbeddingError("embedding_input_budget")
        identity = manifest_identity(self.manifest)
        payload = json.dumps({"config_hash": identity, "sparse_text": text}).encode()
        output = self._request(payload, context=context)
        try:
            result = SparseResult.model_validate_json(output)
        except ValidationError:
            try:
                error = WorkerError.model_validate_json(output)
            except ValidationError:
                raise EmbeddingError("embedding_output_invalid") from None
            raise EmbeddingError(error.code) from None
        if (
            result.config_hash != identity
            or len(result.terms) > self.manifest.max_terms
            or len({term.term for term in result.terms}) != len(result.terms)
            or (text is None and (result.terms or result.truncated))
        ):
            raise EmbeddingError("embedding_output_mismatch")
        return result


class SparseNativeProvider:
    """Only instantiated inside the monitored child. Never normalizes weights."""

    def __init__(
        self,
        directory: Path,
        manifest: SparseModelManifest,
        threads: int,
        *,
        session_factory=None,
    ):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        from app.modules.inference.onnx_cpu import OnnxCpuProvider, _verified_bytes

        if type(threads) is not int or not 1 <= threads <= 4:
            raise EmbeddingError("embedding_thread_budget_invalid")
        verify_assets(directory, manifest)
        self.manifest = manifest
        self.tokenizer = Tokenizer.from_str(
            _verified_bytes(directory, manifest.tokenizer).decode()
        )
        self.tokenizer.no_padding()
        self.tokenizer.no_truncation()
        if self.tokenizer.get_vocab_size() != manifest.vocabulary_size:
            raise EmbeddingError("embedding_sparse_vocabulary_mismatch")
        self.words = {
            index: token
            for token, index in self.tokenizer.get_vocab().items()
            if len(token) <= 128
            and re.fullmatch(r"\w+", token)
            and token == token.casefold()
        }
        self.bounded_tokenizer = Tokenizer.from_str(self.tokenizer.to_str())
        self.bounded_tokenizer.enable_truncation(max_length=manifest.max_tokens)
        payload = _verified_bytes(directory, manifest.graph)
        OnnxCpuProvider._verify_text_graph(payload, manifest.opset)
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = threads, 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_cpu_mem_arena = options.enable_mem_pattern = False
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        self.model = (
            ort.InferenceSession(payload, options, providers=["CPUExecutionProvider"])
            if session_factory is None
            else session_factory(payload, options)
        )
        self.model.disable_fallback()
        expected = {
            manifest.input_name,
            manifest.attention_mask_name,
            manifest.token_type_ids_name,
        }
        inputs = self.model.get_inputs()
        outputs = self.model.get_outputs()
        if (
            {item.name for item in inputs} != expected
            or any(
                item.type != "tensor(int64)" or len(item.shape) != 2 for item in inputs
            )
            or len(outputs) != 1
            or outputs[0].name != manifest.output_name
            or outputs[0].type != "tensor(float)"
            or len(outputs[0].shape) != 3
            or outputs[0].shape[-1] != manifest.vocabulary_size
        ):
            raise EmbeddingError("embedding_signature_mismatch")
        result = self.expand(manifest.canary_text)
        actual = {term.term: term.weight for term in result.terms}
        if any(
            abs(actual.get(term.term, -100) - term.weight) > manifest.canary_tolerance
            for term in manifest.canary
        ):
            raise EmbeddingError("embedding_canary_mismatch")
        if OnnxCpuProvider._dynamic_batch(self.model):
            batch = self.expand_many([manifest.canary_text, manifest.canary_text])
            expected = {term.term: term.weight for term in result.terms}
            for row in batch:
                actual = {term.term: term.weight for term in row.terms}
                if (
                    row.truncated != result.truncated
                    or actual.keys() != expected.keys()
                    or any(
                        abs(actual[term] - weight) > manifest.canary_tolerance
                        for term, weight in expected.items()
                    )
                ):
                    raise EmbeddingError("embedding_canary_mismatch")

    def expand(self, text: str | None) -> SparseResult:
        if text is None:
            return SparseResult(
                config_hash=manifest_identity(self.manifest), terms=[], truncated=False
            )
        return self.expand_many([text])[0]

    def expand_many(self, texts: list[str]) -> list[SparseResult]:
        from app.modules.inference.onnx_cpu import OnnxCpuProvider

        if not 1 <= len(texts) <= 8 or any(
            not isinstance(t, str) or not 0 < len(t) <= 16384 for t in texts
        ):
            raise EmbeddingError("embedding_input_budget")
        if len(texts) > 1 and not OnnxCpuProvider._dynamic_batch(self.model):
            return [self.expand(text) for text in texts]
        import numpy as np

        manifest = self.manifest
        full = self.tokenizer.encode_batch(texts)
        encoded = self.bounded_tokenizer.encode_batch(texts)
        length = max(len(item.ids) for item in encoded)
        # Token ID 0 is masked; special-token vocabulary identity remains unchanged.
        values = {}
        mask = np.zeros((len(texts), length), dtype=np.int64)
        ids = np.zeros_like(mask)
        types = np.zeros_like(mask)
        for row, item in enumerate(encoded):
            ids[row, : len(item.ids)] = item.ids
            mask[row, : len(item.ids)] = item.attention_mask
            types[row, : len(item.ids)] = item.type_ids
        values = self.model.run(
            [manifest.output_name],
            {
                manifest.input_name: ids,
                manifest.attention_mask_name: mask,
                manifest.token_type_ids_name: types,
            },
        )[0]
        if (
            not isinstance(values, np.ndarray)
            or values.shape != (len(texts), length, manifest.vocabulary_size)
            or not np.isfinite(values).all()
        ):
            raise EmbeddingError("embedding_output_invalid")
        weights = np.max(np.log1p(np.maximum(values, 0)) * mask[:, :, None], axis=1)
        results = []
        for row, tokens in zip(weights, full, strict=True):
            ranked = sorted(
                (
                    (self.words[index], min(float(weight), 10.0))
                    for index, weight in enumerate(row)
                    if weight > 0 and index in self.words
                ),
                key=lambda item: (-item[1], item[0]),
            )
            results.append(
                SparseResult(
                    config_hash=manifest_identity(manifest),
                    terms=[
                        SparseTerm(term=term, weight=weight)
                        for term, weight in ranked[: manifest.max_terms]
                    ],
                    truncated=len(tokens.ids) > manifest.max_tokens,
                )
            )
        return results
