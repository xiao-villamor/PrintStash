# Single-pass CPU preparation for CPU and GPU rendering

ASCII STL materialization now decodes each source once. The improvement happens
before render dispatch, so both CPU-only and GPU-enabled execution consume the
same faster loader. Binary STL, OBJ, STEP, render settings and admission policy
are unchanged.

The new matched 250-source GPU pair completed in **511.449 s before / 497.296 s
after (2.77% less elapsed time)**. Geometry loading dropped **15.34%**, container
CPU time **3.84%**, and all 250 metadata rows and 238 encoded images matched.
This is a modest whole-pipeline improvement, not a claim that every remaining
CPU bottleneck has been solved.

## Mechanism and memory

Previously ASCII STL was scanned to count facets and then parsed again to fill
an exactly sized array. A third pass over that array calculated bounds. The
materializer now retains the canonical reader's bounded float64 blocks during
one complete validated read, reuses a single block or concatenates several, and
calculates the same bounds. The temporary blocks are released before consumers
calculate topology or prepare rendering.

The canonical parser still owns syntax, finite coordinates, line/source/facet
limits, deadline and source-identity checks. No partial result escapes before
EOF and snapshot validation. Binary input still uses its declared count for
exact allocation. No new queue, worker, dependency, render recipe or memory
allowance was introduced.

Concatenation briefly holds both blocks and output: **144 bytes per facet** for
these numeric buffers, plus bounded parser/object overhead. This is more loader
scratch than the former two-pass ASCII allocation, within the existing guarded
whole-pipeline per-face envelope. Hard limits remain active. Measured complete
worker and container peaks did not increase in the probes below; this is not a
claim that the loader itself needs no extra temporary memory.

## Evidence, from narrow to complete

### Deterministic regression

The 8,192-facet probe initially decoded 16,384 facets in two full passes and
failed its one-decode assertion. After the change it decoded 8,192 once. The
real-I/O integration assertion now requires one full ASCII read, plus the
existing format probes, rather than two. The narrow timing changed from 0.277
to 0.173 s; this small cold-import measurement is diagnostic only.

### All 130 STL sources from the fixed cohort

The differential probe alternated before/after order by source index, in one
process with NumPy already imported. All 130 results matched: 118 materialized
triangle buffers (SHA-256), bounds and counts, and the same 12 invalid sources.
The clock includes loading and the buffer digest used for validation; it is not
an isolated syscall or parsing-kernel timestamp.

| Work, accumulated seconds | Before | After |
| --- | ---: | ---: |
| All 130 STL, wall spans | 41.287 | 22.276 |
| ASCII / invalid-text subset, wall spans | 39.479 | 20.578 |
| Binary subset, wall spans | 1.807 | 1.698 |
| All 130 STL, process CPU | 41.265 | 22.233 |

The all-STL wall reduction is **46.05%**. Small changes to the unchanged binary
path are measurement variation, not attributed to this patch.

### CPU-only complete guarded workers

Three valid ASCII STL originals (small, medium, large; largest 319,628 facets)
ran through supervised geometry measurement and 512×384 WebP generation with
`VAULT_COMPUTE_MODE=cpu`, no GPU device and no network. Each source had a fresh
guarded child. The parent/container setup is outside the timed interval.
Two repetitions used before/after, then after/before order. All metadata and
encoded-image hashes match across all executions.

| Three-source CPU-only run | Before | After |
| --- | ---: | ---: |
| Repetition 1, seconds | 21.670 | 15.211 |
| Repetition 2, seconds | 21.325 | 15.308 |
| Sum across repetitions, seconds | 42.995 | 30.519 |
| Largest reported worker RSS across repetitions, MiB | 226.1 | 224.2 |

That is **29.02% less elapsed time for this three-ASCII-source probe**. It is not
a new 250- or 1000-source CPU comparison and must not be extrapolated to OBJ/CAD.

### Fresh 250-source GPU-enabled comparison

Same immutable selection as the previous report:
`2780f43fa04318c7c8ebbc0b127e250f5887e0b3f4382f2d8800e4d72ccd3824`.
130 STL, 111 OBJ, 9 STEP; 808,880,155 bytes. Cached originals, zero R2 requests,
four HTTP upload threads and a window of 40 pending Artifacts after the first
four cold sources. WSL as Linux user `local`, same observers, width 512 and the
existing quality-85/method-2 WebP profile. No other benchmark or test suite ran
concurrently with these two timed deployments.

| Metric | Before | After |
| --- | ---: | ---: |
| Cohort monotonic seconds | 511.449 | **497.296** |
| Cohort UTC seconds | 511.449 | **497.296** |
| Accumulated geometry-load phase, seconds | 232.605 | **196.924** |
| Accumulated STL loader span, seconds | 80.693 | **48.565** |
| Accumulated general OBJ/STEP loader span, seconds | 151.681 | 148.135 |
| Accumulated measurements, seconds | 76.953 | 77.715 |
| Container CPU seconds | 1445.878 | **1390.343** |
| Accumulated native admission wait, seconds | 543.382 | 495.233 |
| Whole-pool native claims | 48 | 52 |
| Mean active native slots, out of 4 | 1.572 | 1.548 |
| Sampled container RAM peak, MiB | 1959.547 | **1933.949** |
| Sampled supervised child-tree RSS peak, MiB | 680.867 | 680.316 |
| Physical adapter VRAM peak, MiB | 1530 | 1525 |
| Verified originals / metadata rows | 250 / 250 | 250 / 250 |
| Valid encoded images / same invalid sources | 238 / 12 | 238 / 12 |

Accumulated spans overlap and are not additive elapsed time. General-loader
startup and resource coordination still dominate much of the run. The native
claim policy was not changed: its deadline-sensitive preflight produced slightly
different whole-pool claim counts, so it would be wrong to attribute those counts
to a new admission algorithm. This is one sequential GPU pair on a shared host,
not a randomized multi-run estimate; the repeated CPU probe and eliminated
second parse provide the clearer evidence for the mechanism.

All nine metadata fields match the canonical baseline for every row. The 238
WebP images are **byte-for-byte identical between the fresh runs**, matched by
original SHA-256, all 512×384 RGBA with valid alpha and clear borders. Encoded
size remains 2,277,688 bytes. No OOM, swap, source changes or R2 requests. Both
test-owned deployments were stopped after verification.

The earlier shared-source report recorded **466.162 s monotonic / 509.649 s
UTC**. These new runs agree between clocks; its old monotonic result should not
be mixed with their durations to claim a speedup or regression. The controlled
comparison for this patch is 511.449 → 497.296 s. The historical clock discrepancy
is retained in the original report rather than corrected after the fact.

## Immutable runtime provenance

Only `backend/app/modules/media/stl_reader.py` differs between the two runtime
source manifests. Both images derive from the same prior shared-source image.
Parent commit: `f2f850b67b09d3f38c21bc33622540c3a11730e5`.

| | Before | After |
| --- | --- | --- |
| Image | `sha256:86007223bf2b646a6789ff30e4ddaac29e4774a11e7eff5b22ab6cdc8870ae45` | `sha256:e15e2459fc5ad5a45c3d4b1922616fbf16bc1f4670b3677640fdad789597237a` |
| Source manifest | `dcd3d18f7e99d4d78a6e4dc76cbc9096cb01df5fed866f87a4de065e98523a9f` | `f39e716e293c452a46170a36c6fd9395c23b44f9cc01c57d1a9d7774886c0910` |

The immutable contexts, source patch, source manifests, observers, CPU-only
probe and detailed traces are retained locally with the benchmark methodology.
No commit or CI was started before the completed measurements showed a gain.

## Validation matrix

Paths are relative to `backend/tests/`.

| Behaviour | Evidence | Status |
| --- | --- | --- |
| Decode each source facet once | `unit/modules/media/test_stl_reader.py::TestMaterializeStl::test_decodes_source_facets_once`, red then green for ASCII | ✅ |
| One complete ASCII read through real I/O | `integration/modules/media/test_stl_reader.py::TestSTLBudgets::test_materializes_with_bounded_complete_read_passes` | ✅ |
| Exact materialized arrays and measurements across blocks / formats | `TestMaterializeStl::test_preserves_materialized_source_facets`; 130-source differential | ✅ |
| Reject invalid tail after complete blocks | `TestMaterializeStl::test_refuses_invalid_tail_after_complete_blocks` | ✅ |
| Preserve facet budget refusal | `TestMaterializeStl::test_refuses_ascii_facet_budget_exhaustion` | ✅ |
| Reject source replacement during reading | `TestMaterializeStl::test_refuses_replaced_source_during_materialization`; existing snapshot suite | ✅ |
| Preserve parser limits and malformed-source contracts | Existing unit/integration STL reader suites, 151-case focused pass | ✅ |
| CPU-only output and guarded-memory parity | Two three-source before/after probes, identical metadata/images | ✅ |
| GPU-enabled end-to-end output and original integrity | Fresh real-HTTP 250-source pair, all metadata and images match | ✅ |
| Full CPU cohort rerun | Earlier user preference retained; targeted CPU probe used instead | ⏭️ |

General validation and latest-commit CI results are recorded in
[PR #430](https://github.com/xiao-villamor/PrintStash/pull/430).

[Detailed sanitized comparison](compute-cpu-preparation-250-summary.json) ·
[HTML pipeline report](compute-pipeline-report.es.html) ·
[Previous shared-source report](compute-shared-source-250.md)
