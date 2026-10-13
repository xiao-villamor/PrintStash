# Unified image coverage

Container cases run against the built image on each native architecture before
the release workflow promotes its digest to release tags: `bash backend/scripts/test-unified-image.sh
printstash:local`. The script runs the unittest cases, then verifies the full
storage adapters and a restart with persistent files.

Repository contracts run with `cd backend && uv run pytest
tests/repo/test_unified_container.py tests/repo/test_ci_workflows.py -q`.
In the table, `image` means `backend/unified/tests/test_image.py::TestUnifiedImage`,
`supervisor` means `backend/tests/repo/test_unified_container.py::TestUnifiedSupervisor`,
`compose` means `backend/tests/repo/test_unified_container.py::TestUnifiedCompose`,
and `workflow` means `backend/tests/repo/test_ci_workflows.py::TestUnifiedImageWorkflow`.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | serves the application on one port | Happy | Fresh volume, full image | SPA HTML and healthy API on port 3000 | E2E | ✅ image::test_serves_the_application_on_one_port |
| 2 | retains database state across replacement | Happy | Write SQLite sentinel, replace container | Sentinel survives | E2E | ✅ image::test_retains_database_state_across_replacement |
| 3 | runs services as the configured identity | Edge | PUID/PGID 12345 | nginx/API processes use 12345 | E2E | ✅ image::test_runs_services_as_the_configured_identity |
| 4 | serves SPA deep links | Edge | Request /settings | SPA HTML | E2E | ✅ image::test_serves_spa_deep_links |
| 5 | returns 404 for missing assets | Error | Missing asset request | HTTP 404 | E2E | ✅ image::test_returns_404_for_missing_assets |
| 6 | shuts down on stop | Happy | Healthy container, Docker stop | Exit 0 within grace period | E2E | ✅ image::test_shuts_down_on_stop |
| 7 | exits when a service exits | Error | Kill API or nginx | Container exits nonzero | E2E | ✅ image::test_exits_when_a_service_exits |
| 8 | rejects invalid startup configuration | Error | Invalid nginx size, root PUID, inaccessible DB path | Container exits nonzero | E2E | ✅ image::test_rejects_invalid_startup_configuration |
| 9 | exits when a child stops | Edge | Either child exits 0 or 7 | Exit status preserved; sibling stopped | Integration | ✅ supervisor::test_exits_when_a_child_stops |
| 10 | shuts down on SIGTERM | Happy | Signal supervisor | Both children stop; exit 0 | Integration | ✅ supervisor::test_shuts_down_on_sigterm |
| 11 | rejects invalid nginx configuration | Error | nginx validation fails | No services start; exit 1 | Integration | ✅ supervisor::test_rejects_invalid_nginx_configuration |
| 12 | configures standalone deployment | Happy | Default Compose | One service; five mounts; port 3000; restart policy | Integration | ✅ compose::test_configures_standalone_deployment |
| 13 | accepts deployment overrides | Edge | Fork image, version, port | Rendered Compose has requested values | Integration | ✅ compose::test_accepts_deployment_overrides |
| 14 | requires smoke test before exporting digest | Error | Publishing workflow | Smoke test precedes digest promotion | Integration | ✅ workflow::test_requires_smoke_test_before_exporting_digest |
| 15 | CI exercises unified container | Happy | Pull-request build | Container suite is required | Integration | ✅ workflow::test_ci_exercises_unified_container |
| 16 | Bake uses authenticated Actions cache | Happy | CI and publish workflows | Bake action supplies cache authentication for checkout builds | Integration | ✅ workflow::test_bake_uses_authenticated_actions_cache |
| 17 | full image survives restart with storage adapters | Happy | Full image; custom UID/GID; five persistent folders | Adapters initialize; files survive restart; API restart exits container | E2E | ✅ backend/unified/tests/test-runtime.sh |
