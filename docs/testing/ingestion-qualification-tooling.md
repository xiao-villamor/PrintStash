# Ingestion qualification toolchain

The lowest-direct core lane selected Pyright 1.1.403 alongside the supported NumPy 2.5.3. Its 2,382 functional cases and Ruff passed, but the retired checker reported six scalar-comparison type errors. Backend and core development metadata now require Pyright 1.1.414, the current release; the lock already resolved that version, so runtime dependencies and mesh output do not change.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | Backend excludes retired NumPy type checker | Error | Backend development dependency metadata | Pyright 1.1.403 cannot resolve; current 1.1.414 remains allowed | Repo | ✅ `tests/repo/test_python_runtime.py::TestPythonRuntime::test_excludes_retired_numpy_type_checker[backend]` |
| 2 | Core excludes retired NumPy type checker | Error | Core development dependency metadata | Pyright 1.1.403 cannot resolve; current 1.1.414 remains allowed | Repo | ✅ `tests/repo/test_python_runtime.py::TestPythonRuntime::test_excludes_retired_numpy_type_checker[core]` |

The two pure metadata contracts passed by direct invocation; that is not a pytest-suite result. Ruff, formatting and offline lock regeneration passed. Ordinary PR CI supplies the full test run. Final Deep CI remains on GitHub after the corrective PR merges to main. The ongoing sustained qualification remains in its immutable checkout; this development-only floor does not change its runtime environment.
