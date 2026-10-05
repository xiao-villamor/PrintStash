# ADR-0013: Evaluate a disposable prepared-geometry cache before adoption

Status: Accepted — do not adopt a persistent prepared-geometry cache with the current evidence.

## Decision

Keep production geometry preparation unchanged after completing a disposable
cache pilot. A faster warm array read alone does not justify a
production cache. Adoption requires measured reuse across actual consumers,
bounded storage and memory, and a complete recovery and integrity contract.
This decision can be revisited when that evidence is available.

Original Artifact bytes remain authoritative. The existing raw-Artifact
materialization cache serves verified source bytes; it is separate from a cache
of interpreted geometry. Neither cache is a derivative receipt, a source of
volume evidence, or proof that a fingerprint is complete.

## Current reuse

The normal mesh derivative request computes metadata, thumbnail and optional
fingerprint in one supervised child. It reuses the loaded geometry or retained
scene within that request; retained scene materialization can reuse admitted
buffers through rendering and analysis. A failed STL renderer can deliberately
release buffers before streaming and reload for fingerprinting. That exceptional
path is distinct from the normal preparation cost.

| Consumer | Current ownership | Possible repeated preparation |
| --- | --- | --- |
| Primary metadata, thumbnail and fingerprint | One mesh request and child; shared geometry or retained scene | No independent parse per output in the normal path |
| Resumed fingerprint continuation | New supervised request after basic outputs survived a failed optional phase | Source preparation can repeat |
| Independent visual or point embeddings | Separate supervised analysis consumers; views share preparation inside one request | Source interpretation can repeat between requests |
| Geometry verification | Separate supervised consumer | Preparation can repeat for the verified pair |
| On-demand viewer STL | Separate supervised conversion with its own derivative recipe | Preparation can repeat after ingestion |

Measure how often these paths actually revisit the same source and interpretation.
Do not multiply the primary ingestion load by its output count to estimate reuse.

## Pilot boundary

The candidate stores owned placed float64 vertices and int64 faces in a private,
disposable directory. The cold path uses the current `mesh_loading.load_mesh`
contract and Trimesh binary STL export; the warm path reconstructs the same
candidate arrays and executes the same consumer. Source bytes, geometry and output
checks must accompany timing and memory measurements. Export byte equality does
not qualify viewer precision refusals or staged storage publication.

A historical mixed pilot used `process=True`. The current loader uses
`process=False`; those historical timings are not a current baseline. The new
comparison must run both sides with the current preparation and consumer policies
in real supervised children, including startup, admission and decoding costs.

A flattened 3MF candidate does not represent the production retained-scene
contract. It cannot substitute for versioned unique resources, placements,
reflections, instance identity, or complete-versus-sampled geometry facts. Nor does
it define a B-rep cache: tessellation inputs and capability refusal semantics
would require their own representation and identity. The pilot does not claim a
benefit for large sources until an admitted current-flow measurement proves one.

## Current-stack measurement

Decision: **do not introduce a persistent prepared-geometry cache**. The normal
ingestion attempt already shares its preparation, and actual cross-job hit
frequency has not been measured. This optional study is complete; a future
adoption proposal must meet the gates below.

The canonical command used `--repeat 10 --cold-runs 3` on six synthetic cases.
All **216 observations completed**: 60 creation trials and 156 source/cache
consumer trials with identical reference STL digests. Each of the six candidate
entries preserved exact source arrays; every source hash remained unchanged. Loader baseline: `52bc2ffe139a37d99703daa6621236d86bb1786d`; the
benchmark checkout was dirty because this experiment was not yet committed.
Python 3.14.8, NumPy 2.5.3, SciPy 1.18.1 and Trimesh 5.1.1;
OMP/OpenBLAS threads were both set to 1. This shared host is **not a qualified
performance gate**. Raw reports remain disposable local benchmark artifacts.

| Case | Warm source + export ms | Verified cache + export ms | Create + cleanup ms | Modeled warm hits to repay creation | Fresh source worker total ms | Fresh cache worker total ms | Cache bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `cube-binary.stl` | 1.172 | 1.579 | 98.97 | No payoff | 2786.1 | 2321.5 | 2,003 |
| `cube-mm.3mf` | 2.539 | 1.758 | 104.62 | 134 | 1848.4 | 1688.2 | 1,330 |
| `multiple-build.3mf` | 2.437 | 1.576 | 97.93 | 114 | 1573.6 | 1569.2 | 1,811 |
| `reflected-build.3mf` | 2.193 | 1.601 | 90.40 | 153 | 1596.9 | 1624.9 | 1,330 |
| `sphere_5120.stl` | 3.925 | 3.635 | 98.59 | 341 | 1679.9 | 1651.6 | 492,382 |
| `sphere_81920.stl` | 59.965 | 50.967 | 128.78 | 15 | 1582.3 | 1602.8 | 7,865,186 |

Values are medians, not latency guarantees. The warm repayment calculation is
`ceil(create / (source - cache))` for a positive saving; it excludes eviction,
leases, recovery and cold misses. It is a modeled requirement, not an observed
user hit count. Cold medians have only three samples per method, uncontrolled
filesystem temperature, admission and different imports; they cannot establish
a production ingestion speedup. The two larger STL caches are approximately
1.92 times their source size.

The small STL regresses on warm reads. Small 3MF cases require 114–153 warm
reuses to repay writes; 5,120-face STL needs 341 and 81,920-face STL needs 15.
Fresh-worker results do not show a consistent win. Peak RSS is a high-water
mark (247.9–293.5 MiB across recorded samples), not incremental
cache memory. Virtual memory, isolated parse/read phase costs, SQL publication
and the full-flow reuse distribution were not measured.

Exact benchmark source SHA-256: `14660f90d4ff40b108c4427967baec82f2bbd297626987e7e4ec2693ddba7969`.
Canonical raw report SHA-256: `7482f4a1503d68fb5eb345e95e4df021248c88ecc4166891f2bedbe0c781596c`.

## Adoption gates

A production proposal must establish all of the following:

- A versioned interpretation identity derived from source content, parser and
  preparation semantics, precision, and relevant tessellation or sampling inputs.
  Output recipe versions invalidate outputs; they are not parser identities.
- A manifest published atomically with validated array shapes, dtypes, bounds,
  checksums and byte limits. Unsupported, corrupt or mismatched entries rebuild
  from authoritative source bytes without granting geometry authority.
- Explicit leases for readers and writers, bounded disk quota and memory
  admission, safe garbage collection, and recovery from interrupted writes,
  concurrent builds and process death. Eviction cannot remove an active reader.
- Preservation of retained-scene and complete-versus-sampled contracts wherever
  those consumers need them; no authority inferred from flattened arrays alone.
- Current-flow cold and warm comparisons plus observed reuse sufficient to pay
  for cache writes, validation, storage and recovery. Include misses and refused
  work; do not report only successful warm reads.

Until those gates are verified, the pilot remains optional and production keeps
its existing preparation path. This is a completed no-adoption decision for the current evidence, not a
claim that prepared caching cannot help a future qualified workload.
