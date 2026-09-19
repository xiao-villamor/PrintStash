# Native import stage closeout

M15 audits the revised M08–M14 migration boundary and removes the last complete
Python implementation that duplicated an accepted Rust compute kernel. It does
not claim that the original all-native plan is complete: STEP and enrichment
remain blocked by the library qualification results in `native-step.md` and
`native-enrichment.md`.

## Ownership after closeout

| Stage | Compute language | Coordinator | Durable-state owner | Closeout |
| --- | --- | --- | --- | --- |
| G-code metadata and BGCODE | Rust `gcode-core` and Prusa `libbgcode` adapter | Python typed facade | Python import job and Artifact records | Complete; no Python parser/codec fallback |
| Archive inspection and extraction | Rust `archive-core` | Python selection facade | Python staging leases and import job | Complete; no Python ZIP policy/extractor fallback |
| Mesh preparation and previews | Rust renderer and native 3MF resources | Python media policy and component graph | Python thumbnail generations and Artifacts | Complete for M10; bounded compatibility fallbacks remain only where the native path explicitly refuses an input |
| STEP | Open CASCADE through the existing supervised Python helper | Python media coordinator | Python analysis generations | Blocked; no qualified Rust OCCT binding fits the 2 GiB profile |
| Geometric similarity | Rust voxel, descriptor, proximity, ICP, exact-proof, and decision kernels | Python deterministic hypothesis/result coordinator | Python fingerprints and similarity runs | Complete; M15 removes the duplicate Python BVH, ICP, and dead voxel projection |
| HTTP(S) acquisition | Rust Reqwest streamer | Python URL/provider/inbox policy | Python acquisition journal, staging leases, and jobs | Complete for the revised boundary; every server-side import body uses the native streamer |
| Optional enrichment | Native ONNX Runtime and Rust tokenizer through the existing supervised Python worker | Python enrichment coordinator | Python generation and asset records | Blocked; the maintained Rust ORT wrapper is still prerelease and trails the selected stable runtime |

The Python coordinator remains the intentional application seam. Queue,
transactions, publication, storage adapters, authentication, and HTTP routing
were retained after M03–M07 were deferred. No Python callable is counted as
native merely because the coordinator invokes it from a native thread.

## Import entry-point audit

| Entry point | Bytes/compute path | Result |
| --- | --- | --- |
| Direct API upload | Python HTTP body boundary, then native format stages | Preserved; HTTP routing remains Python by architecture |
| `/ingest/url` and background URL imports | Rust one-hop pinned streaming for every validated redirect | Native acquisition |
| Public provider page/file import | Python metadata resolver returns a direct URL; Rust streams the body | Native acquisition |
| Connected provider selection | Python rotates credentials and returns a short-lived URL; Rust streams the body | Native acquisition without moving secrets into a second owner |
| Browser capture and upload slots | Python HTTP/storage receipt boundary; native archive/G-code/mesh stages after staging | Preserved boundary |
| Inbox direct and resolved assets | Shared Rust URL streamer; native archive expansion where applicable | Native acquisition and archive compute |
| Portable library archive | Local staged input; Rust archive policy/extraction and native format stages | Native compute with Python publication transaction |
| External LibrarySource | Existing read-only `StorageBackend` materialization, then native format stages | Preserved because M07 was deferred |

## Removed duplication

`SurfaceProximity` now requires `SurfaceTree` from the native extension. Missing
or incompatible native builds fail with `native_similarity_unavailable` rather
than silently selecting a second BVH and ICP implementation. The unused Python
voxel projection implementation was also removed. This deletes 252 lines of
production numerical code and leaves one tested implementation of each accepted
kernel.

The retained mesh preview fallbacks serve inputs the primary renderer refuses
and preserve established bounded-output behavior. They are observable policy,
not an alternate implementation selected when the native extension is absent.

## Coverage matrix

| # | Behaviour | Category | Observable outcome | Tier | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | uses only native closest-surface queries | Happy | Triangle interiors, edges, vertices, subdivided surfaces, work budgets, and owned geometry retain their results | Core | ✅ `tests/mesh/similarity/test_proximity.py::TestSurfaceProximity` |
| 2 | fails clearly without the native similarity kernel | Error | Missing or older extension produces `native_similarity_unavailable`; no Python compute starts | Core | ✅ `TestSurfaceProximity::test_fails_clearly_without_native_kernel` |
| 3 | translates native alignment failures | Error | Stable `GeometryError` crosses the typed boundary | Core | ✅ `TestSurfaceProximity::test_translates_native_alignment_rejection` |
| 4 | preserves native nearest-neighbour and exact-proof failures | Error | Native validation codes become stable `GeometryError` values | Core | ✅ `test_geometry.py::{TestNearestNeighbors::test_translates_native_neighbor_errors,TestEquivalentTriangles::test_translates_native_equivalence_errors}` |
| 5 | cleans partial native archive output | Error | A later extraction failure removes every prior staged output | Core | ✅ `test_archives.py::TestExtractSelectedFailures::test_removes_prior_outputs_when_native_extraction_fails` |
| 6 | preserves unrelated native archive errors | Error | Non-policy errors are not mislabeled as archive policy failures | Core | ✅ `test_archives.py::TestInspectArchive::test_preserves_unrelated_native_errors` |
| 7 | preserves similarity application records | Happy/Edge | Fingerprints, candidate processing, cache, and verification decisions pass through real backend integration | Integration | ✅ 324 focused backend tests |
| 8 | keeps autonomous core coverage above its ratchets | Coverage | 2,036 tests pass; aggregate branch coverage is 99.18%; changed and inherited native-stage boundary modules clear their floors | Core coverage | ✅ official coverage lane |
| 9 | packages native stages for amd64/arm64 full/lite images | Packaging | Declared capabilities import and execute in every image | CI | ❌ pending milestone PR CI |
| 10 | preserves every supported application import flow | E2E | Upload, URL, provider, capture, inbox, portable, and external sources produce the expected Artifacts | E2E/Playwright | ❌ pending milestone PR CI |

## Performance comparison

M15 removes fallback code that was unreachable on a valid production build, so
the expected native-path timing and outputs are identical to M14. The committed
`geometric-similarity-v1` comparison remains the relevant workload: it verifies
fingerprints and all verification classes before recording latency, CPU, RSS,
and container memory against both the immediate parent and M00 baseline.

The controlled 2 CPU/2 GiB and 4 CPU/4 GiB comparison must run from the M15 PR
revision. Neutral results are acceptable; any repeatable regression above the
standard thresholds blocks delivery. No local timing is presented as controlled
evidence.

## Verification and remaining delivery gates

- Core Pyright: clean.
- Focused archive/similarity tests: 104 passed.
- Autonomous core coverage: 2,036 passed plus all five coverage auditors;
  99.18% aggregate branch coverage.
- Backend fingerprint/similarity integration: 324 passed.
- Rust 1.98.1 format and Clippy: passed for the binding and `render-core`.
- `render-core`: 11 tests passed. Binding unit tests: 7 passed with the PyO3
  interpreter and library path pinned to the backend Python 3.11 environment.

Full backend/frontend/browser, native coverage, security, container, architecture,
and controlled-performance gates remain milestone-PR evidence. M15 can merge
into the integration branch only after those current-revision checks pass.
