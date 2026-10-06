# Independent Python compatibility phases

The ordinary compatibility suite consumed 56 minutes before its serial resource
pass began. Combining both under one 60-minute job cancelled the external pass
without a complete compatibility verdict. Run the two disjoint phases in
independent jobs, preserving their markers, slow cases, work-stealing for ordinary
tests, serial service startup for resource tests, and required successful results.
The full lane and backend coverage remain composed as before.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | Runs compatibility phases independently | Happy | Deep CI workflow | Both phase jobs present; fail-fast disabled; both mandatory; 60-minute limit retained | Repo | ✅ `tests/repo/test_ci_workflows.py::TestDeepSuite::test_runs_compatibility_phases_independently` |
| 2 | Selects the full phase in one invocation | Edge | ordinary/resources CLI lanes; recording uv boundary | Exactly one pytest invocation with the correct marker expression, root and parallel/serial mode | Repo | ✅ `tests/repo/test_ci_workflows.py::TestDeepSuite::test_selects_the_full_phase_in_one_invocation` |
| 3 | Provisions real PID namespaces for each full suite | Error | Backend and compatibility workflow steps | Required namespace setup precedes tests; cleanup runs even after failure | Repo | ✅ `tests/repo/test_ci_workflows.py::TestDeepSuite::test_provisions_real_pid_namespaces_for_each_full_suite` |

| 4 | Runs CI with current Python | Happy | Current runtime workflow and compatibility phases | Current interpreter remains pinned; compatibility invokes its selected phase before unconditional cleanup | Repo | ✅ `tests/repo/test_python_runtime.py::TestPythonRuntime::test_runs_ci_with_current_python` |

The new regressions fail before implementation: **3 failed in 2.95s**. The affected
workflow contract file passes **47 cases in 9.93s** after implementation. Ruff,
shell syntax and whitespace checks pass. No local full, coverage or Deep CI was run.
A collected-case marker audit is separate evidence about partition completeness;
it does not execute those cases or establish full compatibility success.

The collected-case audit sees **19,418 full cases**, partitioned into **18,935
ordinary** and **483 resource** cases, with zero overlaps, missing cases or extra
cases. Collection does not execute them. Counts describe this branch's current
suite and are not substituted for the historical 18,931 passing executions.

The first PR CI exposed an additional runtime-policy assertion expecting the retired compatibility command: **1 failed, 5,774 passed in 164.53s**. The focused workflow/runtime selection reproduces that failure (**1 failed, 64 passed**) and passes **65 cases in 9.47s** after aligning that assertion with the selected phase. These selections overlap the earlier workflow checks and are not added together.
