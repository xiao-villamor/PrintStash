# Provider transport test isolation

Deep CI on fd2002bc found a pooled HTTP client from an ended event loop when a later unit test called transport shutdown. Its traceback reaches a real httpcore connection and the closed loop. The suite already isolated the shared general HTTP client, but not the provider client pool or host limiters.

The shared test fixture now installs fresh provider dictionaries per test. It never closes an inherited client on another loop. The owning test/application lifecycle still has to close its own clients before its loop ends. This patch does not assert that all existing tests clean up their sockets, suppress close failures, or change production lifecycle behavior. It prevents cross-test cache inheritance and restores host capacity. The regressions exercise the real fixture and HTTPX close path with an explicit loop-bound transport; no network is needed.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | New test does not close a client from an ended loop | Edge | Inherited HTTPX client with closed owning loop | New fixture shutdown succeeds; inherited transport untouched | Repo | ✅ `tests/repo/test_provider_transport_isolation.py::TestProviderTransportIsolation::test_new_test_does_not_close_a_client_from_an_ended_loop` |
| 2 | New test starts with available host capacity | Edge | Inherited host semaphore exhausted | Fresh test host limiter admits work | Repo | ✅ `tests/repo/test_provider_transport_isolation.py::TestProviderTransportIsolation::test_new_test_starts_with_available_host_capacity` |
| 3 | Current test closes its own client on its live loop | Happy | Current HTTPX client with live loop | Client and transport closed; current cache empty | Repo | ✅ `tests/repo/test_provider_transport_isolation.py::TestProviderTransportIsolation::test_current_test_closes_its_own_client_on_its_live_loop` |
| 4 | Current test close failure is not suppressed | Error | Current client with invalid closed owning loop | Original RuntimeError propagates | Repo | ✅ `tests/repo/test_provider_transport_isolation.py::TestProviderTransportIsolation::test_current_test_close_failure_is_not_suppressed` |

Baseline regressions: **2 failed, 2 passed in 2.98s**, reproducing inherited closed-loop shutdown and exhausted host capacity. Final focused regressions plus provider transport unit tests: **45 passed in 3.18s**. Ruff/format/whitespace pass. No local full, coverage or Deep CI ran. The initial command with the wrong working directory ran no tests and is not counted as a reproduction.

The same Deep run also has two setup errors from the fairness fixture, with four of69 foreground uploads still pending at the unchanged110s bound. Those remain unresolved by this patch. Backend total:18,944 passed, one failure, two errors in3518.05s; coverage gate and resource phase not reached. Other21 remote jobs succeeded; none is rerun here.
