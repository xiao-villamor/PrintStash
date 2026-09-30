# Mesh pipeline regression acceptance — #259

The gate runs the shipped full image, its real HTTP endpoints, SQLite, filesystem
and DBOS engine in native Linux containers capped at 1 GiB and 4 GiB, with swap
disabled. Deep CI covers amd64 and arm64 independently. Host-side factories
generate adversarial input; neither the image nor CI downloads issue attachments.

The matrix lists implemented assertions. Container rows require a successful
resource report on the final integration SHA; a test existing is not evidence
that a particular CI run passed. The containment, outcomes and staging changes
supply the companion tests referenced here.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | preserves supported mesh results (Gate.run) | Happy | STL, OBJ, 3MF, native STEP/STP; DXF original-only | Supported geometry/viewer output; original bytes preserved | Production container | ✅ implemented |
| 2 | preserves 3MF placement (TestThreeMFResources) | Edge | Nested, repeated, mirrored instances | Exact transformed geometry | Integration | ✅ implemented |
| 3 | rejects excessive expansion (test_refuses_exponential_component_expansion) | Error | Small package expands above face budget | scene_resource_limit | Integration/container | ✅ implemented |
| 4 | rejects malformed packages (TestMalformedPackages/TestProductionResources) | Error | Cycles, invalid XML, missing parts, bombs | Stable failure codes | Integration/container | ✅ implemented |
| 5 | stops sudden allocation (Gate.burst) | Error | 8 GiB allocation, 128 MiB worker AS | Resource exit; API healthy; OOM count unchanged | Production container | ✅ implemented |
| 6 | bounds aggregate allocation (test_counts_descendants_against_the_admitted_budget) | Edge | Nested child memory and concurrent uploads | Tree refusal; cgroup OOM count unchanged | Integration/container | ✅ implemented |
| 7 | preserves changing admission (TestRenderAdmission) | Edge | Active work during concurrency change | New admission waits for existing work | Unit | ✅ implemented |
| 8 | terminates worker trees (TestWorkerBootstrap/TestAbandonedTemporaryOutputs) | Error | Parent death, timeout, successful orphan | Descendants dead; owned temporary output removed | Integration/unit | ✅ implemented |
| 9 | reports geometry refusal (TestDeriveMesh) | Error | Resource refusal with embedded image | Metadata terminal failure | Integration | ✅ implemented |
| 10 | retains embedded preview (TestDeriveMesh) | Happy | Same input | Thumbnail ready | Integration | ✅ implemented |
| 11 | suppresses unchanged failures (TestTerminalMeshFailure/Gate.run) | Edge | Watcher, scans, nudges, restart | Attempts and timestamps unchanged | Integration/E2E/container | ✅ implemented |
| 12 | exhausts timeouts (TestOutcomes) | Error | Repeated timeout | Stops at configured maximum | Integration | ✅ implemented |
| 13 | permits deliberate reprocessing (TestWithdrawAndRetry/TestPending/TestTerminalMeshFailure) | Edge | Retry, new recipe, changed bytes | New work eligible | Integration | ✅ implemented |
| 14 | preserves downloads (Gate.case/TestMeshFailureRecovery) | Error | Failed derivative | SHA-256 equals original | E2E/container | ✅ implemented |
| 15 | preserves slicer handoff (Gate.case/TestMeshFailureRecovery) | Error | Failed derivative | Signed download equals original | E2E/container | ✅ implemented |
| 16 | retains failed input (TestStagingCleanup/Gate.run) | Error | Uncommitted failed ingest | Lease/input retained until discard/expiry | Integration/container | ✅ implemented |
| 17 | releases committed staging (TestStagingCleanup/Gate.case) | Happy | Committed import/recovery | Staging absent, no residual leases | Integration/container | ✅ implemented |
| 18 | preserves uncertain ownership (TestStagingCleanup) | Error | Replacement path | Replacement survives, lease charged | Integration | ✅ implemented |
| 19 | serializes discard/retry (TestStagingDiscard/TestStagingCleanup) | Edge | Concurrent database writers | Coherent ownership outcome | SQLite/PostgreSQL integration | ✅ implemented |
| 20 | denies another user (TestDiscardStaging) | Error | Different owner | 404, no deletion | Integration API | ✅ implemented |
| 21 | exposes safe recovery (zip-upload.spec.ts) | Happy | Failed ZIP inspection | Confirmation, discard, capacity released | Playwright real | ✅ implemented |
| 22 | avoids accumulation (Gate.run) | Edge | Twelve mixed files after native warm-up | Current RSS delta and trend bounded | Production container | ✅ implemented |
| 23 | continues after bad input (Gate.run/TestMeshFailureRecovery) | Error | Bad file followed by healthy one | Following metadata ready | E2E/container | ✅ implemented |

## Running the production gate

From backend/, build the same image used in production, then run:

```sh
docker build --build-arg INSTALL_PROFILE=full -t printstash:mesh-resources .
uv run --frozen python scripts/mesh_resource_gate.py \
  --image printstash:mesh-resources --memory-gib 1 --report /tmp/mesh-1g.json
uv run --frozen python scripts/mesh_resource_gate.py \
  --image printstash:mesh-resources --memory-gib 4 --report /tmp/mesh-4g.json
```

For private acceptance of the two reported files, put their actual 3MF packages
in a local directory and add --acceptance /path/to/directory. Exactly two .3mf
files are required. At 4 GiB their metadata must be ready; at 1 GiB controlled
terminal refusal is allowed. Never commit those attachments or depend on
GitHub downloads in CI.

The JSON artifact records original hashes, wall time, actual job attempts,
derivative attempts/reasons/duration/peak RSS, cgroup peak/current memory,
OOM-kill count, warmed current parent RSS samples, and cleanup outcome.
Derivative attempts can equal the configured maximum after a terminal refusal;
actual job attempts distinguish that exhaustion marker from executions.
The artifact is written on failure too, along with container logs. Missing
reports fail artifact publication. A following healthy input must finish.

CI can be dispatched on an integration branch so all four independent PRs can
be validated together. Require CI and Deep CI on the exact integration SHA
before considering a release; this gate does not authorize publication.
