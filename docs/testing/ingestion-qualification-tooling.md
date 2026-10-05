# Ingestion qualification toolchain

The lowest-direct core lane selected Pyright 1.1.403 alongside the supported NumPy 2.5.3. Its 2,382 functional cases and Ruff passed, but the retired checker reported six scalar-comparison type errors. Backend and core development metadata now require Pyright 1.1.414, the current release; the lock already resolved that version, so runtime dependencies and mesh output do not change.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | Backend excludes retired NumPy type checker | Error | Backend development dependency metadata | Pyright 1.1.403 cannot resolve; current 1.1.414 remains allowed | Repo | ✅ `tests/repo/test_python_runtime.py::TestPythonRuntime::test_excludes_retired_numpy_type_checker[backend]` |
| 2 | Core excludes retired NumPy type checker | Error | Core development dependency metadata | Pyright 1.1.403 cannot resolve; current 1.1.414 remains allowed | Repo | ✅ `tests/repo/test_python_runtime.py::TestPythonRuntime::test_excludes_retired_numpy_type_checker[core]` |

The two pure metadata contracts passed by direct invocation; that is not a pytest-suite result. Ruff, formatting and offline lock regeneration passed. Ordinary PR CI supplies the full test run. Final Deep CI remains on GitHub after the corrective PR merges to main. The ongoing sustained qualification remains in its immutable checkout; this development-only floor does not change its runtime environment.


The delivery browser lane also lost its execution context during a cold-start dynamic import. The trace preserves two document requests and no completed API response. Its harness now statically imports the real download helper before enabling its submit button, and the dedicated Vite runner scans that harness as its explicit dependency entry. The existing actual browser proof additionally requires exactly one document navigation while retaining all redirect, provider-credential, payload and zero-proxy-body assertions. No retries or larger timeouts are introduced. See [Vite dependency entries](https://vite.dev/config/dep-optimization-options#optimizedeps-entries).

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 3 | Native S3 delivery retains its prepared document | Edge | Cold delivery harness with statically imported actual helper | Exactly one document navigation; real 307/provider200, expected bytes and filename, no forwarded provider credentials or proxied body | E2E | ✅ `frontend/tests/e2e-real/delivery/native-download.spec.ts` |

The changed browser fixture is formatted and targeted lint passes. Its actual execution remains pending remote post-merge Deep CI; the original failed trace is preserved.


A static harness entry alone did not prevent a second cold-start reload in GitHub: the actual request was interrupted and the original 120-second deadline failed. The dedicated runner now explicitly prepares the helper's `@tanstack/react-query` dependency and keeps dependency discovery closed during this immutable proof. React dependencies remain prepared by the base configuration. This does not change the product's development runner or disable its normal reload behavior. The browser proof preserves reload messages on failure before propagating the error; no failure is ignored.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 4 | Delivery helper dependencies are prepared | Edge | Actual resolved delivery Vite configuration | Query dependency and React are included before interaction | Repo | ✅ `frontend/tests/repo/delivery-config.test.ts::prepares the authenticated helper's query dependency` |
| 5 | Prepared delivery dependency graph remains fixed | Edge | Actual resolved delivery Vite configuration | Discovery is closed while downloads run | Repo | ✅ `frontend/tests/repo/delivery-config.test.ts::keeps its prepared graph fixed during downloads` |

Resolved configuration assertions passed by direct invocation; that is not a Vitest-suite result. The real browser proof remains required in remote Deep CI after merge. Both failed traces remain preserved.

The actual two-case resolved-configuration Vitest selection passes in the existing DOM-backed frontend test environment (2.70s). The initial Node-only annotation conflicted with the shared DOM setup; it was removed without changing that setup. No backend or native workload was run locally for this correction.


The backend qualification also exposed a network guard leaking from a unit test into a later E2E test. A test override and a manual guard restore used different teardown stacks; the later fixture undo reinstalled the completed test's guard. Socket boundaries now use the owning pytest monkeypatch stack, so overrides and the guard unwind in their original order. Two storage fault-injection tests use scoped contexts instead of undoing the shared fixture early. External-network restrictions and owned S3/PostgreSQL exemptions remain unchanged.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 6 | Restore a completed test's network boundary | Edge | Test overrides DNS, connect or connect_ex; unmarked, S3 and PostgreSQL resource variants | Unmarked test rejects public access; following E2E test receives original boundary for all nine combinations | Repo | ✅ `tests/repo/test_tier_guards.py::TestNetworkGuardLifecycle::test_restores_the_original_boundary_after_a_test_override` |
| 7 | Refuse an overwriting storage provider | Error | Fault-injected provider overwrites a duplicate key | Setup refuses the provider; restored capability remains read-only | Unit | ✅ `tests/unit/modules/storage/storage_opendal/test_backend.py::TestOpenDALStorageBackend::test_setup_refuses_a_provider_that_overwrites_duplicate_keys` |
| 8 | Retry deletion after a worker crash | Error | Worker crashes after verifying an owned deletion intent | Pending verified intent completes on retry; owned file is deleted | Integration | ✅ `tests/integration/modules/storage/test_storage_deletion.py::TestProcessStorageDeleteIntents::test_verified_intent_retries_after_worker_crash` |

The subprocess regression fails against the previous guard and passes after the fix. The focused pytest selection passes **12 cases in 6.72s**, including the nine guard variants, the original pinned-DNS test and both storage fault-injection tests. This is a focused result, not a local full-suite run. The frozen sustained qualification's application and native code are unchanged.
