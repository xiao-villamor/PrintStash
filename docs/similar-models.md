# Review similar Models

Similar Models finds geometric relationships while keeping every Model,
Artifact, G-code Revision, print record, and source file separate. Analysis is
local and off by default. It does not rewrite, repair, move, or deduplicate your
files.

## Start a review

1. In **Settings → Maintenance → Similar models**, enable analysis and save.
2. Choose the library, a Collection, an external source, or selected Models,
   then choose **Start analysis**. Alternatively, open a Model's **Similar** tab
   and choose **Find similar**.
3. Open **Similar models** from the library navigation. Existing results appear
   immediately; a new run adds results progressively.
4. Choose **Compare** to inspect the two previews, dimensions, surface metrics,
   and print context. Both previews use the same physical scale and camera.
   Scale compensation, reflection compensation, and an overlay are optional
   inspection controls; the original dimensions remain visible.
5. **Confirm evidence** records your review. **Reject** suppresses that pair for
   the current algorithm, **Save for later** postpones it, and **Reopen** returns
   it to review.

Confirmation is a recorded judgment, not a file operation. A matching component
or a plate can also create a Multipart Model or add parts to an existing
editable Multipart Model. Existing part choices and quantities are preserved;
conflicting names or repeated choices must be resolved explicitly. Family
creation is unavailable until the separate Family owner is integrated.

## Understand the evidence

| Evidence | Meaning |
|---|---|
| Identical geometry | Full triangle geometry agrees under a rigid transformation. |
| Rescaled | Full triangle geometry agrees after a uniform scale change. |
| Mirrored / rescaled mirrored | Reflection is part of the verified transformation. Symmetric geometry can have an ambiguous reflection history. |
| Remeshed | Surfaces agree closely despite different tessellation. This is approximate evidence. |
| Repaired | Closely matching surfaces have a topology change. Analysis does not repair either file. |
| Similar shape | A geometric resemblance requiring inspection. |
| Component of / plate of | Verified components and their instance quantities describe inclusion or repeated copies. |

Hash matches, B-rep counts, and semantic scores are discovery hints. They do not
establish exact geometric equivalence. Distance measurements use independent
surface samples; “sampled Hausdorff” is not a bound on the continuous surface.
Confidence is an evidence ranking, not a probability that two prints are
interchangeable. Check fit-critical holes and dimensions before printing.

A changed, trashed, purging, or tombstoned source makes the corresponding evidence
stale. Earlier review decisions remain in history. Stale evidence cannot be
confirmed as current. Candidates and counts require edit access to both Models.

## Control work and visibility

The default confidence threshold is 90%. Changing it previews the number of
already measured candidates; it does not start analysis or erase decisions.
Advanced settings provide per-class thresholds, a triangle cap (2,000,000),
verification samples (5,000), candidates per shortlist (20), an optional scan
interval, and local embeddings. These controls have enforced upper bounds.

Analysis on upload is a separate opt-in. A derivative failure leaves a successful
upload intact. Runs keep their checkpoints across restarts and can be cancelled
between work units. Maintenance and storage garbage collection share the normal
application admission and retention controls. Truncated buckets, partial inputs,
and unsupported Artifacts are reported in progress instead of implying a complete
search. Starting a manual analysis retries previously failed, unsupported or
partial geometry, which is useful after increasing a limit or enabling format
support. Scheduled analysis keeps those cached results to avoid retry loops.
Complete fingerprints are reused. Exact comparisons can also reuse their stored
proof after both source digests and the verification recipe are checked.

The repository Benchy and Spatula use complete geometry at the default limits.
An existing explicit lower cap remains unchanged; increase it before a manual
retry. See [mesh processing measurements](mesh-processing-performance.md) for
resource limits, source fixtures and the Rust assessment.

STL, OBJ, and 3MF geometry works in the lite profile. Oversized STL inputs can
produce partial descriptors, which cannot prove exact equivalence. STEP requires
the full profile and runs in an isolated native worker with fixed tessellation
settings, normalized units, and process budgets.

## Optional local semantic neighbors

The full profile includes a CPU ONNX adapter. Weights are operator-supplied;
PrintStash does not download them or send renders to a remote service. Geometry
analysis remains usable when embeddings are disabled or unavailable.

Provide a directory containing `manifest.json`, its declared ONNX graph files,
and (for CLIP) a tokenizer file. Set:

```text
VAULT_EMBEDDING_LOCAL_MODEL_DIR=/models/similarity
VAULT_EMBEDDING_MODEL_KEY=clip-vit-base-patch32-fp32
VAULT_EMBEDDING_ONNX_THREADS=2
```

Mount the directory read-only in a container. Enable local embeddings in the
Similarity advanced settings, then run analysis to validate the model and index
mesh views. The operator-supplied manifest pins every file's SHA-256, tensor
signatures, preprocessing, native dimension, and image/text canaries. Validation
must succeed before the UI reports the capability as available.

A reproducible CLIP example and exact upstream revision are documented in
[the preplaced model fixture](../backend/tests/fixtures/embeddings/README.md).
Image-only DINO manifests support Model-to-Model semantic neighbors. Text queries
require a validated CLIP text tower in the same embedding space as the image
tower. Semantic results link to Models and have no geometric confirmation action.

Vectors retain native dimensions as little-endian float32 in immutable
Space/Generation records. A different model or preprocessing recipe needs a new
explicit generation; incompatible embeddings are never mixed silently. The local
scan uses bounded blocks and reports truncation. It is not a general hybrid or
remote AI search platform.
