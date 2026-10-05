# Prepared geometry cache study

This benchmark-only experiment compares the current public mesh loader with
checksummed float64 vertices/int64 faces restored without processing or welding.
Both paths run the same Trimesh binary STL export and verify its exact digest and
size. It does not activate a cache in ingestion or change Artifact storage.

The CLI owns a temporary private vault; native workers use existing supervision
and memory admission. Reports retain failed samples and source hashes, recipes,
versions and environment limits. Warm operations run in randomized order; cold
operations use fresh supervised workers. “Cold” describes the interpreter, not
a controlled filesystem cache. Creation includes checksumming, fsync, atomic
publication and deletion of the disposable entry.

Peak RSS is a process high-water mark. Cold total includes admission, bootstrap
and child exit, but neither timing includes SQL publication or the complete
ingestion flow. STL equality is not qualification of viewer precision refusals
or staged publication. Flat 3MF arrays are not a retained-scene or B-rep wire
representation. No observed user reuse distribution is claimed.

Run from `backend/` with a new, disposable output directory:

```sh
OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 uv run python -m scripts.bench_prepared_cache \
  --output-dir /tmp/prepared-cache-study --repeat 10 --cold-runs 3
```

Use `--case cube-mm.3mf --repeat 1 --cold-runs 1` for the bounded CLI smoke.
The output directory must not already exist. The canonical observation is its
`report.json`; raw measurements are not a production performance gate.

See [ADR-0013](../adr/0013-prepared-geometry-cache.md#current-stack-measurement)
for six-case results and the completed no-adoption decision.

## Validation

The two mirrored test files passed: **21 parametrized cases**, 11 behaviours (21.96 seconds). Focused suite
hygiene: **17 passed**, 4,573 deselected (15.41 seconds). Scoped Ruff and
Pyright passed with no errors.
Full backend, coverage and scale suites were not run locally for this isolated
benchmark; normal PR CI supplies the repository checks. Deep CI is deferred.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | Reconstructs exact mesh arrays | Happy | Immutable float64 vertices/int64 faces with repeated vertex positions | Exact arrays/order preserved without welding; same STL digest; source mesh unchanged | Unit | ✅ `unit/scripts/test_bench_prepared_cache.py::TestWriteCache::test_reconstructs_exact_mesh_arrays` |
| 2 | Refuses mismatched identity | Edge | Change source SHA/parser/representation/parameters independently | Typed InvalidPreparedCache, original entry unchanged | Unit | ✅ `unit/scripts/test_bench_prepared_cache.py::TestReadCache::test_refuses_mismatched_identity` |
| 3 | Refuses corrupted payload | Error | Cache payload byte corruption | Typed InvalidPreparedCache | Unit | ✅ `unit/scripts/test_bench_prepared_cache.py::TestReadCache::test_refuses_corrupted_payload` |
| 4 | Refuses oversized manifest | Error | Manifest exceeds bounded metadata size | Typed InvalidPreparedCache | Unit | ✅ `unit/scripts/test_bench_prepared_cache.py::TestReadCache::test_refuses_oversized_manifest` |
| 5 | Refuses write beyond byte budget | Error | Budget below raw vertex+face buffers | No cache files written | Unit | ✅ `unit/scripts/test_bench_prepared_cache.py::TestWriteCache::test_refuses_write_beyond_byte_budget` |
| 6 | Refuses read beyond byte budget | Error | Valid entry exceeds reader budget | Typed InvalidPreparedCache before NumPy load | Unit | ✅ `unit/scripts/test_bench_prepared_cache.py::TestReadCache::test_refuses_read_beyond_byte_budget` |
| 7 | Refuses unsafe array metadata/content | Error | Valid payload checksums but wrong shape/object dtype/dangling index/nonfinite vertices | Typed InvalidPreparedCache; no successful reconstruction | Unit | ✅ `unit/scripts/test_bench_prepared_cache.py::TestReadCache::test_refuses_unsafe_arrays` |
| 8 | Cleans owned staging after failed write | Error | Real filesystem plus failure writing second array | Original write exception; no partially published cache or owned staging left | Unit | ✅ `unit/scripts/test_bench_prepared_cache.py::TestWriteCache::test_cleans_owned_staging_after_failed_write` |
| 9 | Preserves existing cache entry | Edge | Second write targets existing entry with different geometry | Refusal; every original entry byte unchanged | Unit | ✅ `unit/scripts/test_bench_prepared_cache.py::TestWriteCache::test_preserves_an_existing_entry` |
| 10 | Preserves parsed 3MF geometry | Happy | Current loader with mm/inch/reflection/multiple build corpus | Original source unchanged, exact cached arrays and exported STL hash parity | Integration | ✅ `integration/scripts/test_bench_prepared_cache.py::TestPrepareSource::test_preserves_parsed_geometry` |
| 11 | Emits one bounded cold/warm pilot observation | Happy | CLI cube-mm.3mf repeat1/cold1 using real supervised child | JSON schema/source/recipes/phase costs, no failures, digest equivalence, no production reuse claim | Integration | ✅ `integration/scripts/test_bench_prepared_cache.py::TestMain::test_emits_one_bounded_cold_warm_observation` |
