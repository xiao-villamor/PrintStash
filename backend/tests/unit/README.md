# `tests/unit` — the function, and nothing else

Nothing here may open a socket or touch the database. `conftest.py` enforces both: a real
connection raises `RealNetworkAccess` naming the test, and asking for `db_session` or
`client` is refused outright.

That is not a purity rule, it is what makes this tier worth having. A unit test that
quietly reaches the network is slow, flaky, and proves something different on a machine
with no DNS — and three of them were doing exactly that before the guard existed.

**Put a test here when:**

- the code is **pure logic** — a parser, a hash, a URL check, a slug helper, a mapping
  from already-built rows;
- the code is a **subprocess worker** with no application state (`stl_preview_worker`,
  `step_worker`);
- the behaviour is a **fault that is hard to reproduce for real** — a timeout, malformed
  provider JSON, a dependency that raises once then succeeds. Patch the egress seam and
  assert the reaction.

**Put it in `integration/` instead when** it touches a row, a router, storage, or RBAC.
The real database is free here; there is no reason to mock it.
