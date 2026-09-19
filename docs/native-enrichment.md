# Native enrichment helper qualification

M14 requires a supervised native helper that replaces the Python inference
worker while preserving PrintStash's model manifests, validation rules, canary
outputs, resource limits, and optional installation boundary. No evaluated Rust
integration currently qualifies under the stable-dependency requirement. The
existing isolated worker remains authoritative; this is a recorded blocker, not
a completed migration.

## Current ownership while M14 is blocked

| Concern | Owner |
| --- | --- |
| ONNX graph execution | Official ONNX Runtime 1.30 through its Python binding, inside the supervised child |
| Tokenization | Hugging Face `tokenizers` 0.23 through its native Rust-backed Python binding |
| Image, point-cloud, dense-text and SPLADE preparation/postprocessing | Existing Python worker using NumPy and the verified model manifest |
| Process reuse, deadlines, RSS ceiling, termination and compute admission | Python inference coordinator and worker pool |
| Model generations, durable progress and vector publication | Existing Python application transactions |

The current worker is not pure Python computation. ONNX Runtime and tokenization
already run in native libraries; Python owns input preparation, output checking,
the bounded pipe protocol, and process supervision. It disables execution
provider fallback, limits intra-op threads to 1–4, uses sequential execution,
disables the CPU memory arena and memory pattern, checks graph signatures and
opsets, forbids external tensor data, validates asset digests, and runs pinned
canaries before accepting a model. A replacement must preserve all of these
controls rather than merely load an ONNX file.

## Libraries evaluated

### `ort` 2.0.0-rc.13 — best technical fit, not accepted

[`ort`](https://crates.io/crates/ort/2.0.0-rc.13) is the maintained safe Rust
wrapper around the ONNX Runtime C API. It is the correct integration direction:
it exposes sessions, tensor values, execution providers, thread options and
dynamic or packaged runtimes without requiring PrintStash to author an unsafe
C wrapper. The published crate requires Rust 1.88 and is MIT OR Apache-2.0.

The current release is still a release candidate. Its default feature set
targets ONNX Runtime API 27 and its package describes compatibility with ONNX
Runtime 1.28, while PrintStash's current stable Python runtime is 1.30. Adopting
the release candidate would either add a second, older ORT distribution to the
full image or depend on an unqualified API-version combination. Both choices
conflict with the migration's stable and latest-compatible dependency rules.

PrintStash will not replace `ort` with hand-written `unsafe` FFI. The
[Rust binding in Microsoft's ONNX Runtime repository](https://github.com/microsoft/onnxruntime/tree/main/rust)
also labels itself experimental, incomplete, and not safe, so copying it does
not improve this decision.

### `tokenizers` 0.23.2 — accepted for a future helper

[`tokenizers`](https://crates.io/crates/tokenizers/0.23.2) is the current stable
Rust package and is the implementation already reached through PrintStash's
Python binding. A future helper should consume the same digest-checked
`tokenizer.json` directly with this crate. No custom BPE, WordPiece, padding, or
truncation implementation is justified.

### Higher-level and alternative runtimes — rejected for this contract

[`fastembed` 7.0.1](https://crates.io/crates/fastembed/7.0.1) is established and
useful for its supported embedding catalogue, but it also uses `ort` and owns a
higher-level model/download contract. PrintStash must execute preplaced,
digest-pinned custom image, text, point-cloud, and SPLADE graphs with its own
manifest identities and canaries. Wrapping or forking FastEmbed would add an
abstraction without removing the underlying `ort` release-candidate gate.

[`tract-onnx` 0.23.7](https://crates.io/crates/tract-onnx/0.23.7) and
[`candle-onnx` 0.11.0](https://crates.io/crates/candle-onnx/0.11.0) are stable
Rust inference projects, but they are different execution engines. Substituting
one would require proving operator coverage and numerical parity across every
accepted graph and canary, and would violate M14's direction to retain official
ONNX Runtime. They remain inappropriate as a silent fallback.

## Acceptance matrix

| # | Behaviour | Category | Required input | Observable qualification result | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | uses a stable maintained Rust binding | Dependency | Current crates.io releases | Stable wrapper supports the selected stable ORT API on amd64 and arm64 | ❌ `ort` remains `2.0.0-rc.13` |
| 2 | validates model assets before execution | Error | Invalid digest, symlink, external tensor, unsupported opset or signature | Stable existing error code; graph never runs | ❌ no native helper integration |
| 3 | preserves dense embedding canaries | Happy | Pinned image, text and point models | Vectors match existing tolerances and normalization | ❌ no native helper integration |
| 4 | preserves sparse expansion | Happy | Pinned SPLADE graph and tokenizer | Terms, weights, truncation and ordering match the canary | ❌ no native helper integration |
| 5 | enforces resource and cancellation bounds | Edge | Slow graph, excess RSS, concurrent interactive/background work | Child is terminated or deferred and the compute lease is released | ❌ no native helper integration |
| 6 | preserves optional packaging | Edge | Full/lite images on amd64 and arm64 | Lite has no required runtime; full validates installed assets | ❌ no native helper integration |
| 7 | improves or preserves performance | Performance | Cold/warm, batches 1–8, every supported modality | Controlled CPU, memory and latency stay within review thresholds | ❌ benchmark waits for a qualifying helper |

## Re-entry gate

Revisit M14 when `ort` publishes a stable release that supports the selected
stable ONNX Runtime line and both Linux architectures without an unreviewed
binary source. The implementation should use `ort` and `tokenizers` directly in
one supervised native process, keep PrintStash's versioned wire envelope, and
reuse the existing compute admission and RSS enforcement. Qualification starts
with invalid-asset and canary tests, then runs cold/warm and batch comparisons
for image, text, point-cloud and sparse models, followed by full/lite amd64 and
arm64 packaging. Until then, the existing Python supervisor and native ORT /
tokenizer libraries remain the safer maintained architecture.
