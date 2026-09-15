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

    def __init__(self, directory: Path, manifest: SparseModelManifest, threads: int):
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
        self.model = ort.InferenceSession(
            payload, options, providers=["CPUExecutionProvider"]
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

    def expand(self, text: str | None) -> SparseResult:
        import numpy as np

        manifest = self.manifest
        terms, truncated = [], False
        if text is not None:
            if not 0 < len(text) <= 16384:
                raise EmbeddingError("embedding_input_budget")
            full = self.tokenizer.encode(text)
            truncated = len(full.ids) > manifest.max_tokens
            encoded = self.bounded_tokenizer.encode(text)
            mask = np.asarray([encoded.attention_mask], dtype=np.int64)
            values = self.model.run(
                [manifest.output_name],
                {
                    manifest.input_name: np.asarray([encoded.ids], dtype=np.int64),
                    manifest.attention_mask_name: mask,
                    manifest.token_type_ids_name: np.asarray(
                        [encoded.type_ids], dtype=np.int64
                    ),
                },
            )[0]
            if (
                values.shape != (1, len(encoded.ids), manifest.vocabulary_size)
                or not np.isfinite(values).all()
            ):
                raise EmbeddingError("embedding_output_invalid")
            weights = np.max(
                np.log1p(np.maximum(values, 0)) * mask[:, :, None], axis=1
            )[0]
            ranked = sorted(
                (
                    (self.words[index], min(float(weight), 10.0))
                    for index, weight in enumerate(weights)
                    if weight > 0 and index in self.words
                ),
                key=lambda item: (-item[1], item[0]),
            )
            terms = [
                SparseTerm(term=term, weight=weight)
                for term, weight in ranked[: manifest.max_terms]
            ]
        return SparseResult(
            config_hash=manifest_identity(manifest), terms=terms, truncated=truncated
        )
