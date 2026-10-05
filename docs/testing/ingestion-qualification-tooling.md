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
