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
