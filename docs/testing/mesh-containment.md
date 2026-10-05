# Mesh containment acceptance matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | refuses sudden allocation | Error | 8 GiB burst under 128 MiB AS | Resource refusal; parent survives | Integration | ✅ `integration/modules/media/test_worker_bootstrap.py::TestWorkerBootstrap::test_refuses_sudden_allocation_before_rss_polling` |
| 2 | starts native dependencies under ceiling | Happy | NumPy/trimesh, 1 GiB AS | Native box geometry succeeds under installed hard ceiling | Integration | ✅ `integration/modules/media/test_worker_bootstrap.py::TestWorkerBootstrap::test_native_dependencies_start_under_hard_ceiling` |
| 3 | kills abandoned workers | Error | Parent killed after child starts | Child absent or dead | Integration | ✅ `integration/modules/media/test_worker_bootstrap.py::TestWorkerBootstrap::test_worker_dies_with_its_parent` |
| 4 | counts descendant memory | Error | Two 100 MiB allocations; 160 MiB allocation | Resource refusal | Integration | ✅ `integration/modules/media/test_worker_bootstrap.py::TestWorkerBootstrap::test_counts_descendants_against_the_admitted_budget` |
| 5 | preserves admission on runtime changes | Edge | One active admission; concurrency becomes two | New capacity waits for active credits to drain | Integration | ✅ `integration/runtime/test_native_admission.py::TestLocalResourcePool::test_waits_for_prior_configuration_to_drain` |
| 6 | retains bounded budgets with estimates disabled | Edge | 1 GiB detected, two workers, estimates off | 512 MiB shared pool | Unit | ✅ `unit/modules/media/mesh_policy/test_admission.py::TestMemoryBudgetBytes::test_disabling_estimates_keeps_whole_safety_budget` |
| 7 | preserves mesh output through isolation | Happy | Cube with thumbnail/fingerprint | Identical measured output | Integration | ✅ `integration/modules/media/test_mesh_isolation.py::TestGenerate::test_matches_the_in_process_engine` |
| 8 | preserves STEP visual rendering | Happy | Native STEP box | Six visual views, worker reaped | Integration | ✅ `integration/modules/media/test_visual_render.py::TestVisualRender::test_renders_step_without_database_access_in_the_child` |
| 9 | reaps timed-out worker groups | Error | Worker spawns child then stalls | Both processes dead | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestSupervise::test_kills_descendants_the_child_started` |
| 10 | rejects isolation bypasses | Error | Production AST | No unapproved raw consumer | Repo | ✅ `repo/test_mesh_boundaries.py::TestMeshBoundaries::test_new_consumers_cannot_bypass_mesh_isolation` |
| 11 | keeps CAD workers free of application DB access | Happy | STEP fingerprint, inaccessible child DB | Geometry and fingerprint succeed | Integration | ✅ TestStepCapacityOwnership.test_fingerprint_worker_does_not_access_application_database |
| 12 | releases CAD capacity after refusal | Error | STEP worker refused at startup | Parent admission existed before launch, reservation released | Integration | ✅ TestStepCapacityOwnership.test_parent_releases_capacity_after_worker_refusal |
| 13 | terminates abandoned descendants | Error | API parent dies with nested native child | Entire worker tree dead | Integration | ✅ TestWorkerBootstrap.test_parent_death_terminates_the_entire_worker_tree |
| 14 | settles child trees before success | Edge | Worker leaves a child after producing output | Reply returned and descendants dead | Integration | ✅ TestWorkerBootstrap.test_success_reaps_descendants_before_returning |
| 15 | retains whole unknown-memory native fallback | Edge | Four workers, memory detection absent | Bounded 2 GiB pool independent of slot count | Unit | ✅ `unit/modules/media/mesh_policy/test_admission.py::TestMemoryBudgetBytes::test_native_fallback_is_independent_of_concurrency` |
| 16 | cleans abandoned owned temporary outputs | Error | Parent killed during native work | Output directory removed | Integration | ✅ TestAbandonedTemporaryOutputs.test_parent_death_cleans_owned_outputs |
| 17 | preserves replaced temporary output | Error | Directory identity changed | Replacement survives cleanup | Integration | ✅ TestAbandonedTemporaryOutputs.test_cleanup_preserves_a_replacement_directory |
| 18 | reaps refused descendants before releasing admission | Error | Tree exceeds admitted RSS | No remaining /proc entries for owned children | Integration | ✅ TestDescendantReaping.test_resource_refusal_reaps_adopted_children |

The API/container OOM, architecture and retention gates are delivered separately
with the permanent regression gate. This matrix covers the containment change.
