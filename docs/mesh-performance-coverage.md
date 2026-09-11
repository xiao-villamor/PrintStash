# Mesh processing performance coverage

This change follows #154 from merged main `77ad5313`. Requirements: ordinary
repository Benchy and Spatula use complete geometry, capture/ingestion shares
the improvement, previews retain quality, and a library above 1,000 Models
keeps bounded work and memory. Explicit operator limits remain enforceable.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| P001 | fingerprints_complete_repository_benchy | Happy | Original 225,706-face STL; defaults | Ready fingerprint, full topology, exact keys, unchanged bytes | Integration | ✅ |
| P002 | verifies_complete_repository_benchy | Happy | Original STL and translated export | Exact geometry evidence with full source | Integration | ✅ |
| P003 | describes_complete_repository_spatula | Happy | Original 3MF | Complete component descriptors, including convex hull | Integration | ✅ |
| P004 | ingests_complete_repository_benchy | Happy | Ingest with similarity enabled | Complete metadata and thumbnail, source preserved | E2E | ✅ |
| P005 | honors_explicit_smaller_analysis_budget | Edge | Benchy above operator limit | Partial evidence cannot claim exact geometry | Integration | ✅ |
| P006 | rejects_invalid_analysis_budgets | Error | Invalid type, zero, above ceiling | Stable validation error before geometry work | Unit | ✅ |
| P007 | preserves_single_component_descriptors | Happy | One connected resource | Whole/component geometric values agree without shared mutable state | Integration | ✅ |
| P008 | preserves_transformed_component_measurements | Edge | Scaled 3MF instance | Resource and whole retain their own physical dimensions | Integration | ✅ |
| P009 | preserves_canonical_retrieval_keys | Happy | Known mesh exports | Existing canonical keys and D2 seed remain identical | Unit | ✅ |
| P010 | preserves_preview_pixels | Happy | Real repository mesh | Optimized render agrees with reference pixels | Integration | ✅ |
| P011 | similarity_resource_probe | Edge | Complete Benchy in 1 GiB cgroup | Complete analysis/render within memory limit | Resource probe | ✅ |
| P012 | keeps_the_nearer_of_two_overlapping_triangles | Edge | Overlapping front/back triangles | Nearer pixels retain expected color/depth | Unit | ✅ |
| P013 | calculates_convex_hull_of_dense_interior | Happy | Known outer solid with interior points | Correct outer volume within bounded work | Unit | ✅ |
| P014 | preserves_hull_under_vertex_reordering | Edge | Permuted source points | Same volume | Unit | ✅ |
| P015 | coplanar_hull_has_explicit_failure | Error | Coplanar/degenerate input | Explicit unavailable geometry | Unit | ✅ |
| P016 | hull_work_cap_stops_analysis | Error | Tiny work budget | Stops with stable resource-limit error | Unit | ✅ |
| P017 | preserves_verification_metrics | Happy | Complete equivalent/changed surfaces | Measurements and evidence classes remain valid | Unit | ✅ |
| P018 | avoids_discarded_similarity_thumbnails | Happy | Fingerprint-only run | Derivatives ready without rendering an unused full thumbnail | Integration | ✅ |
| P019 | admits_dense_geometry_configuration | Happy | Default and explicit dense-mesh cap | Validated setting accepts normal mesh workload | Integration | ✅ |
| P020 | preserves_operator_configuration | Edge | Existing explicit triangle cap | Configured lower limit remains in effect | Integration | ✅ |
| P021 | similarity_cold_benchmark | Edge | More than 1,000 Models | Bounded pages, restart/cache reuse, no unbounded mesh retention | Cold-library probe | ❌ full 1,001-Artifact run pending |
| P022 | preserves_capture_artifact_content | Happy | Captured mesh through shared ingestion | Complete derivative; source digest/provenance unchanged | E2E | ✅ |
| P023 | reuses_verified_unchanged_pairs | Happy | Second scan of verified inputs | Existing exact evidence reused, no repeated geometry work | Integration | ✅ |
| P024 | rechecks_increased_sample_count | Edge | Increased requested sample count | Fresh verification required | Integration | ✅ |
| P025 | rejects_corrupted_cached_source | Error | Stored bytes no longer match digest | Existing evidence cannot bypass source validation | Integration | ✅ |

| P026 | uses_shared_dense_budget_for_step | Happy | Actual isolated STEP worker | Shared ceiling reaches the worker; B-rep volume preserved | Integration | ✅ |
| P027 | detects_effective_nested_cgroup_limit | Edge | Service and parent slice limits | Smallest applicable limit controls admission | Unit | ✅ |
| P028 | ignores_unusable_cgroup_membership | Error | Absent/unsafe/deep membership | Host limit remains available without path traversal | Unit | ✅ |
| P029 | rechecks_reassigned_artifact | Edge | Artifact attached to a different Model | Old candidate proof cannot suppress new pair analysis | Integration | ✅ |
| P030 | rechecks_deleted_artifact | Edge | Source purged after fingerprint read | No cache hit | Integration | ✅ |
| P031 | rechecks_approximate_evidence | Edge | Similar shape observation | Exact-proof cache cannot reuse approximate evidence | Integration | ✅ |
| P032 | rechecks_invalid_cached_recipe | Error | Missing/invalid/old verifier recipe | Fresh verification required | Integration | ✅ |
| P033 | rechecks_invalid_sample_recipe | Error | Boolean/text/excessive sample count | Fresh verification required | Integration | ✅ |
| P034 | releases_loaded_mesh_before_reclaim | Edge | Fingerprint-only request finishes | Loaded arrays released before memory reclamation | Integration | ✅ |
| P035 | saves a complete dense-mesh budget | Happy | Settings input 2,000,000 | Valid form submits the requested cap | UI | ✅ |
| P036 | openapi_contract | Happy | Public settings schema | Existing API shape remains compatible | Repository | ✅ |

| P037 | preserves_full_face_when_only_allocation_chunk_is_small | Edge | Large face fits total work but exceeds one temporary block | Entire face rendered across bounded blocks | Unit | ✅ |
| P038 | preserves_default_cumulative_pixel_budget | Edge | Default RasterBudget | Original 1,000,000-pixel work allowance retained | Unit | ✅ |

Measured baselines and the Python/Rust decision will be recorded separately;
profiling numbers are evidence about execution cost, not test assertions.
