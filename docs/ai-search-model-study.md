# Text model evaluation for #166

These measurements use a frozen, original engineering corpus: 32 library Subjects,
32 labelled retrieval queries (including Spanish and typo cases), and two separate
out-of-domain probes. The corpus was authored for regression testing before the
measurements. It is **not** a human-labelled user study. Native recall below uses
cosine similarity over real model outputs; it is separate from the full hybrid
retrieval gate.

All inference ran locally through PrintStash's monitored ONNX worker, with one CPU
thread, on an x86_64 virtual development host reporting `QEMU Virtual CPU version
2.5+`. Models were preplaced from exact public repository revisions and every
downloaded file was checked against its published digest. No corpus text or
query was sent to a hosted inference API. Timings include local worker IPC but
exclude the search database and HTTP/UI; they do not prove the 100k-vector or
physical Raspberry Pi acceptance targets.

| Model / export | Dimensions | Native recall@5 | Warm query p50 / p95 | Cold verification |
|---|---:|---:|---:|---:|
| BGE-small-en-v1.5 float32 | 384 | 31/32 | 58.7 / 76.5 ms | 2.53 s |
| all-MiniLM-L6-v2 float32 | 384 | 29/32 | 30.0 / 40.0 ms | 1.68 s |
| multilingual-e5-small float32 | 384 | 32/32 | 51.0 / 66.6 ms | 8.81 s |
| nomic-embed-text-v1.5 float32 | 768 | 31/32 | 155.4 / 242.1 ms | 7.34 s |
| mxbai-embed-large-v1 quantized ONNX | 1024 | 31/32 | 358.1 / 508.1 ms | 4.91 s |

The sample is small and candidate differences of one query are not enough to
select a new default. **BGE remains the curated English baseline.** E5 merits a
larger multilingual evaluation. The other entries were evaluated as preplaced
exports; they are not silently added to the download catalog. Nomic was evaluated
at its native dimension; reduced dimensions require its separately specified
layer-normalization step and are not certified by this run. Mxbai's quantized
encoder is distinct from quantizing the stored search index.

Primary model sources: [BGE](https://huggingface.co/BAAI/bge-small-en-v1.5/tree/5c38ec7c405ec4b44b94cc5a9bb96e735b38267a),
[MiniLM](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/1110a243fdf4706b3f48f1d95db1a4f5529b4d41),
[E5](https://huggingface.co/intfloat/multilingual-e5-small/tree/614241f622f53c4eeff9890bdc4f31cfecc418b3),
[Nomic](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5/tree/e9b6763023c676ca8431644204f50c2b100d9aab),
[Mxbai](https://huggingface.co/mixedbread-ai/mxbai-embed-large-v1/tree/b33106f585b9ce46904ad7443a3b52b7a63e231c).

## Candidates outside this measured runtime set

- [EmbeddingGemma](https://huggingface.co/google/embeddinggemma-300m/tree/57c266a740f537b4dc058e1b0cda161fd15afa75)
  requires manual acceptance of its gated model terms. No acceptance was
  performed, and its primary repository has no ONNX export. No quality or latency
  is claimed.
- [Qwen's primary model](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/tree/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3)
  has no primary ONNX export. The public
  [ONNX Community export](https://huggingface.co/onnx-community/Qwen3-Embedding-0.6B-ONNX/tree/c25a394dd583836952667c12f008335071b3f43d)
  was downloaded and digest-verified. Its 613,527,539-byte int8 graph requires
  position IDs and 56 past-key/value inputs, plus last-token pooling. These are
  outside the admitted sentence-tower signature. Inspection found no external
  tensors; a decoder-aware adapter would still need its own canaries and resource
  tests. No inference measurement or support claim is attached to this export.
- [Jina v3](https://huggingface.co/jinaai/jina-embeddings-v3/tree/ab036b023d30b4d1138c4c3bfa9f0c445ab455d6)
  uses CC-BY-NC-4.0. The primary ONNX requires 2,291,339,168 bytes of external tensor
  data, which this runtime deliberately rejects. Its 1,147,151,362-byte fp16 file
  also exceeds the per-asset admission limit. No candidate was run with remote
  code or custom native libraries to bypass these constraints.

## Reproduction and limits

Exact manifests, canaries, prefixes, opsets, asset SHA-256s, measured file sizes,
query timings, corpus hashes and missed query IDs are recorded in
`backend/tests/fixtures/search/text-candidate-manifests.json` and
`text-candidate-measurements.json`. Extract the desired manifest next to its exact
files, then run from `backend/`:

```sh
uv run python -m tests.fakes.text_candidate_benchmark \
  --model-dir /absolute/path/to/preplaced/model \
  --output /tmp/text-candidate-measurements.json
```

The command never downloads models. It checks assets and canaries through the
production provider and keeps query inputs in memory. Repeat `--model-dir` to
compare candidates. An output marked `runtime_rejected` is a failed admission,
not a zero-quality measurement.

The committed real-BGE vector replay also exercises actual authorization,
SQLite FTS5, the ranked LIKE fallback and RRF: hybrid recall@5 **31/32**, BM25
**28/32**, LIKE **20/32**. Both out-of-domain queries returned results at the
generic 0.35 floor. Per-Space thresholds need a larger labelled calibration set;
these probes must not be reported as successful no-match detection.
