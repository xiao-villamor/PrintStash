# Mesh containment acceptance matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | refuses sudden allocation | Error | 8 GiB burst under 128 MiB AS | Resource refusal; parent survives | Integration | ✅ `integration/modules/media/test_worker_bootstrap.py::TestWorkerBootstrap::test_refuses_sudden_allocation_before_rss_polling` |
| 2 | starts native dependencies under ceiling | Happy | NumPy/trimesh, 1 GiB AS | Worker reports installed hard ceiling | Integration | ✅ `integration/modules/media/test_worker_bootstrap.py::TestWorkerBootstrap::test_native_dependencies_start_under_hard_ceiling` |
| 3 | kills abandoned workers | Error | Parent killed after child starts | Child absent or dead | Integration | ✅ `integration/modules/media/test_worker_bootstrap.py::TestWorkerBootstrap::test_worker_dies_with_its_parent` |
| 4 | counts descendant memory | Error | Two 100 MiB allocations; 160 MiB allocation | Resource refusal | Integration | ✅ `integration/modules/media/test_worker_bootstrap.py::TestWorkerBootstrap::test_counts_descendants_against_the_admitted_budget` |
| 5 | preserves admission on runtime changes | Edge | One active admission; concurrency becomes two | New admission waits; same controller | Unit | ✅ `unit/modules/media/mesh_processing/test_admission.py::TestRenderAdmission::test_config_change_waits_for_old_admissions_to_settle` |
| 6 | retains bounded budgets with estimates disabled | Edge | 1 GiB detected, two workers, estimates off | 256 MiB per worker | Unit | ✅ `unit/modules/media/mesh_processing/test_admission.py::TestRenderAdmission::test_disabling_estimates_keeps_divided_safety_budget` |
| 7 | preserves mesh output through isolation | Happy | Cube with thumbnail/fingerprint | Identical measured output | Integration | ✅ `integration/modules/media/test_mesh_isolation.py::TestGenerate::test_matches_the_in_process_engine` |
| 8 | preserves STEP visual rendering | Happy | Native STEP box | Six visual views, worker reaped | Integration | ✅ `integration/modules/media/test_visual_render.py::TestVisualRender::test_renders_step_without_database_access_in_the_child` |
| 9 | reaps timed-out worker groups | Error | Worker spawns child then stalls | Both processes dead | Unit | ✅ `unit/modules/media/test_mesh_isolation.py::TestSupervise::test_kills_descendants_the_child_started` |
| 10 | rejects isolation bypasses | Error | Production AST | No unapproved raw consumer | Repo | ✅ `repo/test_mesh_boundaries.py::TestMeshBoundaries::test_new_consumers_cannot_bypass_mesh_isolation` |

The API/container OOM, architecture and retention gates are delivered separately
with the permanent regression gate. This matrix covers the containment change.
