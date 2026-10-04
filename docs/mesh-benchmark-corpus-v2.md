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

V2 reuses the three revisions, SHA256 hashes and project licensing descriptions recorded in [ADR 0009's external-input inventory](adr/0009-3mf-pilot/external-inputs.json). The binaries are not committed. `--download-external` explicitly fetches those pinned URLs, bounds chunks and total bytes, verifies exact size/hash, fsyncs staging and atomically replaces the destination. A failed fetch/verification preserves an existing destination and removes staging. Setup network transfer is separate from native/pipeline timing.

The three projects assert compatibility without inventing analytic measurements. Their `source_faces`, `resources` and `instances` remain explicitly unknown until an independent source inventory supplies them. Synthetic preview/plate variants isolate those rules; they do not establish that each real project contains every listed feature. Merely listing a reference does not count as benchmarking a materialized real file.

## Recovery scenarios

The manifest records ten named scenarios: corrupted cached bytes, disk full, child SIGKILL, parent SIGKILL, timeout, cancellation while waiting/running/publishing, retry after failure and concurrent regeneration. Each has a required precondition and durable expected contract, and is marked `harness_scenario_not_a_fixture`. This corpus does not claim to inject or execute recovery behavior. A harness must record scenario execution separately before reporting those targets as verified.

## Generation evidence

A local Python 3.12.3 run generated and hash-verified all 71 offline fixtures (163,325,838 bytes) in 6.391 seconds, with sampled process-lifetime self high-water RSS of 27,537,408 bytes. This measures **generation plus verification**, not ingestion latency, worker RSS or a performance gate. It includes existing interpreter imports and filesystem effects; there was one repetition.

Explicit downloads were verified on the same host: PrusaSlicer 42,568 bytes / 0.122 seconds, BambuStudio 23,059 bytes / 0.077 seconds, OrcaSlicer 256,455 bytes / 0.133 seconds. These are download/setup durations, not parsing timings or portable latency claims. Upstream project licensing descriptions remain those in the historical ADR; this change redistributes no slicer binary.

Source SHA256 at this evidence point:

- `scripts/mesh_benchmark_corpus_v2.py`: `ac381a8fcdad6545f1e77aef8d13a49faa1c2695bb6992b0c3629685681dd569`
- `scripts/mesh_corpus_v2_contracts.py`: `e679980587729ba56eafb3e51445231ba496d87b7743614c88aebeab3ad9dc36`
- `scripts/mesh_corpus_v2_externals.py`: `a396c44feef6c076d0a15e83ab7905a73ec1d31fb970785b07c4d5d854dd587a`
- `scripts/mesh_corpus_v2_geometry.py`: `88b3013eac701317b73503563ac6141fac4787b59d77b70cec29e0fd45dd873b`
- `scripts/mesh_corpus_v2_scenes.py`: `feb3e06188aa0c2cf420446186896f4e00c45e5a03720d03856e17e2b0a236dc`

## Coverage matrix

The initial 29 rows were planned before implementation; 17 follow-up rows strengthen source facts and invalid inputs. The final three rows were written before their fixture corrections (three red cases; the full-profile hash test already passed). All 49 functions below passed in the final corpus/owner/hygiene selection (123 parametrized tests including repository checks, 4,234 deselected). Test paths are relative to `backend/tests/`. No full/coverage/Deep gate was run for this slice.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | `test_matches_frozen_manifest` | Happy | Small profile | Exact frozen entries | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestCorpusV2::test_matches_frozen_manifest` |
| 2 | `test_preserves_v1_content` | Happy | V1 generator | All original bytes/hash match | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestCorpusV2::test_preserves_v1_content` |
| 3 | `test_repeats_identical_fixture_bytes` | Happy | Repeated generation | Identical bytes | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestCorpusV2::test_repeats_identical_fixture_bytes` |
| 4 | `test_hashes_actual_generated_inputs` | Happy | All small fixtures | File lengths and SHA256 match | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestCorpusV2::test_hashes_actual_generated_inputs` |
| 5 | `test_describes_required_families` | Happy | Catalog | All source/geometry/precision/load families | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestCorpusV2::test_describes_required_families` |
| 6 | `test_retains_profile_boundaries` | Edge | Small profile | Full fixtures remain deferred | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestCorpusV2::test_retains_profile_boundaries` |
| 7 | `test_encodes_binary_policy_cases` | Edge | Binary variants | Headers/counts/normals/attributes/degenerates/trailing | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestBinaryFixtures::test_encodes_binary_policy_cases` |
| 8 | `test_encodes_nonfinite_coordinates` | Error | NaN/Inf coordinates | Declared refusal and actual nonfinite float | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestBinaryFixtures::test_encodes_nonfinite_coordinates` |
| 9 | `test_encodes_ascii_policy_cases` | Edge | ASCII variants | Whitespace/long line/empty/multiple/incomplete | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestAsciiFixtures::test_encodes_ascii_policy_cases` |
| 10 | `test_matches_analytic_geometry` | Happy | Closed analytic shapes | Volume and bounding box from emitted coordinates | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestGeometryFixtures::test_matches_analytic_geometry` |
| 11 | `test_marks_uncertifiable_volume_unknown` | Edge | Open/overlapping/nonmanifold | No asserted solid volume | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestGeometryFixtures::test_marks_uncertifiable_volume_unknown` |
| 12 | `test_encodes_topology_cases` | Edge | Topology variants | Required face/vertex construction | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestGeometryFixtures::test_encodes_topology_cases` |
| 13 | `test_encodes_core_cases` | Edge | Core variants | Actual XML structure and target policies | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_encodes_core_cases` |
| 14 | `test_encodes_production_cases` | Edge | Production variants | Actual component/path/transform structure | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_encodes_production_cases` |
| 15 | `test_encodes_preview_cases` | Edge | Synthetic project variants | Valid/broken/absent preview payloads | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_encodes_preview_cases` |
| 16 | `test_retains_float64_source_coordinates` | Edge | Coordinates near 1e12 | XML retains distinct precise coordinates | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestPrecisionFixtures::test_retains_float64_source_coordinates` |
| 17 | `test_encodes_equivalent_units` | Happy | Six supported units | Equivalent physical target bounds | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestPrecisionFixtures::test_encodes_equivalent_units` |
| 18 | `test_retains_permuted_geometry` | Edge | Reordered indices/faces | Same referenced positions | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestPrecisionFixtures::test_retains_permuted_geometry` |
| 19 | `test_writes_exact_grid_counts` | Happy | Bounded generated grids | STL count and length match | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestLoadFixtures::test_writes_exact_grid_counts` |
| 20 | `test_bounds_write_chunks` | Edge | Large generated grid | Maximum write <=64KiB | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestLoadFixtures::test_bounds_write_chunks` |
| 21 | `test_encodes_many_instances` | Edge | Repeated shared resource | One resource and many placements | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestLoadFixtures::test_encodes_many_instances` |
| 22 | `test_bounds_compressed_xml` | Edge | Highly compressed archive | Declared uncompressed bytes under ceiling | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestLoadFixtures::test_bounds_compressed_xml` |
| 23 | `test_reuses_pinned_pilot_references` | Happy | ADR0009 metadata | Identical revisions/hashes/licenses | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_externals.py::TestExternalFixtures::test_reuses_pinned_pilot_references` |
| 24 | `test_requires_explicit_download` | Edge | Default generation | No external materialization | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_externals.py::TestExternalFixtures::test_requires_explicit_download` |
| 25 | `test_verifies_external_hash` | Happy | Explicit boundary fetch | Verified file atomically materialized | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_externals.py::TestExternalFixtures::test_verifies_external_hash` |
| 26 | `test_rejects_external_size_limit` | Error | Oversized response | Refusal before commit | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_externals.py::TestExternalFixtures::test_rejects_external_size_limit` |
| 27 | `test_preserves_existing_file_on_download_failure` | Error | Incorrect downloaded hash | Existing file unchanged; staging removed | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_externals.py::TestExternalFixtures::test_preserves_existing_file_on_download_failure` |
| 28 | `test_records_required_failure_scenarios` | Error | Recovery catalog | Explicit preconditions/expected durable outcomes | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestRecoveryScenarios::test_records_required_failure_scenarios` |
| 29 | `test_rejects_changed_fixture` | Error | Changed bytes | Hash validation refusal | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestVerifyManifest::test_rejects_changed_fixture` |
| 30 | `test_encodes_root_relationship` | Edge | Non-default root plus decoy | Relationship selects bounded independent model | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_encodes_root_relationship` |
| 31 | `test_retains_printable_flag` | Edge | Legacy false item | Explicit compatibility flag and translation | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_retains_printable_flag` |
| 32 | `test_encodes_invalid_index` | Error | Triangle index 999 | Actual out-of-range index | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_encodes_invalid_index` |
| 33 | `test_encodes_duplicate_ids` | Error | Two objects | Same resource ID twice | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_encodes_duplicate_ids` |
| 34 | `test_encodes_cross_part_reference` | Happy | Production component path | Referenced member actually contains cube | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_encodes_cross_part_reference` |
| 35 | `test_includes_production_tracking_ids` | Happy | Production package | Unique IDs on required elements; child build empty | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_includes_production_tracking_ids` |
| 36 | `test_encodes_nested_translations` | Happy | Two nested components plus build | Translations10/20/40 encoded | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_encodes_nested_translations` |
| 37 | `test_encodes_affine_linear_cases` | Edge | Reflection or singular transform | Exact linear matrix coefficients | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_encodes_affine_linear_cases` |
| 38 | `test_encodes_component_cycle` | Error | Objects2 and3 | Reciprocal component references | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_encodes_component_cycle` |
| 39 | `test_encodes_exponential_expansion` | Error | 21 binary assembly levels | Expanded faces exceed declared cap | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_encodes_exponential_expansion` |
| 40 | `test_encodes_unknown_required_namespace` | Error | Unknown prefix/URI | Required namespace is actually unimplemented URI | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_encodes_unknown_required_namespace` |
| 41 | `test_encodes_valid_png_payload` | Happy | Generated PNG | CRC and decompressed pixels valid | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_encodes_valid_png_payload` |
| 42 | `test_encodes_synthetic_plate_metadata` | Edge | Two plate entries | Actual config and two build placements | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_encodes_synthetic_plate_metadata` |
| 43 | `test_retains_foreign_metadata` | Edge | Unrelated keys | Foreign metadata values retained | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_retains_foreign_metadata` |
| 44 | `test_refuses_invalid_grid_before_output` | Error | Invalid type/size/count | No bytes written before refusal | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestLoadFixtures::test_refuses_invalid_grid_before_output` |
| 45 | `test_writes_large_ascii_counts` | Happy | 20k-face ASCII grid | Exact facet/vertex count and complete grammar | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestLoadFixtures::test_writes_large_ascii_counts` |
| 46 | `test_rejects_invalid_geometry_expectation` | Error | Invalid state/type/counter/bounds | Constructor rejects inconsistent expectation | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_contracts.py::TestExpectationValidation::test_rejects_invalid_geometry_expectation` |
| 47 | `test_transforms_have_analytic_placements` | Happy | Nested/reflected source with second fixed build | Independent XML algebra certifies bbox90/80,24faces,2instances,volume16000 | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_transforms_have_analytic_placements` |
| 48 | `test_resolves_all_zip_part_content_types` | Happy | Every synthetic3MF package member | Each OPC part resolves its Default/Override ContentType includingconfig | Unit | ✅ `unit/scripts/test_mesh_corpus_v2_scenes.py::TestSceneFixtures::test_resolves_all_zip_part_content_types` |
| 49 | `test_full_profile_matches_frozen_catalog` | Happy | Full profile generated once | Exact complete frozenJSON; actualsixlargefilehashes/lengths match | Unit | ✅ `unit/scripts/test_mesh_benchmark_corpus_v2.py::TestCorpusV2::test_full_profile_matches_frozen_catalog` |
