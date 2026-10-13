# Mesh benchmark corpus v2

The v2 corpus covers source policy, analytic geometry, precision and load families without changing application parsers. Its manifest records **target contracts**, never observed compliance. V1's generator and frozen bytes/hashes are preserved; v2 includes all 14 v1 inputs and adds 51 small inputs. Full materialization adds six larger files. Recovery entries describe harness scenarios rather than input bytes.

## Materialization

Run from `backend/`:

```sh
uv run python -m scripts.mesh_benchmark_corpus_v2 --output-dir /tmp/mesh-corpus-v2
uv run python -m scripts.mesh_benchmark_corpus_v2 --output-dir /tmp/mesh-corpus-v2 --full
uv run python -m scripts.mesh_benchmark_corpus_v2 --output-dir /tmp/mesh-corpus-v2 --download-external
```

Default generation is offline and small. `--full` enables binary STL grids with exactly 5,000 / 20,000 / 200,000 / 1,000,000 / 2,000,000 faces plus a 20,000-face ASCII grid. These planar grids exercise reading, preparation and load limits; their zero-thickness surface has no certified solid volume. They do not stand in for dense closed geometry or certify fingerprint/render performance.

`build_contract_corpus(root, *, full=False, download_external=False)` returns only materialized fixtures, plus pinned external references and recovery scenarios. Fixture fields remain compatible with v1 (`filename`, `file_type`, SHA256, input bytes, faces/resources/instances and expectation), with `family` and `profile` added. `benchmarks/mesh/corpus-v2.json` freezes the complete **full, offline** catalog. A small-profile run compares its entries with the catalog filtered by `profile == "small"`. Deferred entries must not be reported as inputs actually benchmarked. Downloaded inputs are additional `profile == "download"` entries.

STL grids stream through at most 64 KiB buffers. Counts/types are checked before writing; the ceiling is 2,000,000 faces. Hash verification reads in 64 KiB chunks. Small geometry and XML inputs stay bounded; the highly compressed XML contains approximately one MiB of harmless comment padding, below two MiB uncompressed. ZIP member order, timestamps and permissions are fixed.

## Ownership and public API

`mesh_corpus_v2_contracts.py` owns the closed dataclasses/enums without importing generators. `mesh_corpus_v2_externals.py` owns pinned metadata and explicit streaming downloads. Geometry and XML generators stay in their existing separate owners. `mesh_benchmark_corpus_v2.py` retains orchestration, recovery scenario definitions, verification and CLI, with explicit public reexports to preserve its existing callers. Tests import their canonical owners and share only corpus/materialized-model fixtures through `tests/unit/scripts/conftest.py`.

## Inputs and independent expectations

| Family | Inputs and target contract |
|---|---|
| Binary STL | Original normal/`solid` headers, truncation and count mismatch; trailing bytes refused; supplied normals/attributes do not change geometry; degenerate facets leave solid volume unknown; NaN/Inf refused |
| ASCII STL | Exponent/whitespace variation, an 8 KiB solid-name line, incomplete facet, empty input, two disjoint solids; full-profile large ASCII grid |
| 3MF Core | Six units; a root relationship selecting a non-default model while a decoy is present; multiple build items; unused vertices/parts; empty geometry, invalid indices and duplicate IDs; legacy `printable` filtering |
| Assemblies/Production | Cross-part references, nested translation plus a fixed cube (bbox90mm), reflection plus a translated cube (bbox80mm), cycles, singular transformation, exponential expansion exceeding two million faces, unknown required namespace URI |
| Synthetic project metadata | Valid PNG, broken PNG, missing preview, two-plate metadata, auxiliary geometry and foreign metadata. These are generated fixtures, **not exports from slicer applications** |
| Geometry | Cube; radius-one octahedral sphere tessellation (volume 4/3); square torus with a through hole (volume 12); open and reversed meshes; remote 0.125 mm component; thin closed solid; flat surface; overlapping solids; duplicate positions; nonmanifold edge |
| Precision | Micron-scale solid, six physically equivalent unit encodings, coordinates already translated to 1e12 in float64 XML, million-mm dimensions, reordered faces/vertices |
| Load | Original 12-face cube, full-profile grids through two million faces, one shared resource placed 2,048 times, bounded highly compressed XML |

Closed-shape volumes come from cube/prism algebra and the octahedron formula. Unknown topology, overlapping solids, reversed winding and flat/open surfaces explicitly carry `volume_mm3: null` with `volume_contract: "unknown"`; no certified union or solid volume is inferred. Equivalent-unit expectations are physical millimeters, independent of parser output. Source face counts describe unique stored triangles, while expected triangle counts include build instances.

The `printable` case preserves the documented [legacy slicer compatibility policy](adr/0009-3mf-loader-capabilities.md); it is not asserted to be a Core requirement. The cross-part fixture includes tracking UUIDs on its build, item, objects and component, and has an empty child build, consistent with the [Production extension](https://github.com/3MFConsortium/spec_production/blob/master/3MF%20Production%20Extension.md). The [Core specification](https://github.com/3MFConsortium/spec_core/blob/master/3MF%20Core%20Specification.md) supplies unit definitions and transform conventions. Refusal rules also include application policies (trailing bytes, resource limits and singular transforms); they do not label every refusal as a specification violation.

## Real slicer projects

V2 reuses the three revisions, SHA256 hashes and project licensing descriptions recorded in [the external-input inventory](../backend/benchmarks/mesh/external-inputs.json). The binaries are not committed. `--download-external` explicitly fetches those pinned URLs, bounds chunks and total bytes, verifies exact size/hash, fsyncs staging and atomically replaces the destination. A failed fetch/verification preserves an existing destination and removes staging. Setup network transfer is separate from native/pipeline timing.

The three projects assert compatibility without inventing analytic measurements. Their `source_faces`, `resources` and `instances` remain explicitly unknown until an independent source inventory supplies them. Synthetic preview/plate variants isolate those rules; they do not establish that each real project contains every listed feature. Merely listing a reference does not count as benchmarking a materialized real file.

## Recovery scenarios

The manifest records ten named scenarios: corrupted cached bytes, disk full, child SIGKILL, parent SIGKILL, timeout, cancellation while waiting/running/publishing, retry after failure and concurrent regeneration. Each has a required precondition and durable expected contract, and is marked `harness_scenario_not_a_fixture`. This corpus does not claim to inject or execute recovery behavior. A harness must record scenario execution separately before reporting those targets as verified.
