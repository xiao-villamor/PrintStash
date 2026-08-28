# `tests/repo` — invariants about the repository itself

Not about behaviour: about the shape of the codebase. These are the checks that catch a
change nobody's feature test would.

- **`test_openapi_contract.py`** — the OpenAPI snapshot. A route added, renamed or
  re-shaped without regenerating it is a silent break for every generated client.
- **`test_forbidden_imports.py`** — the import boundaries. `printstash_core` must not
  import `app`, and the layering below that must hold.
- **`test_ci_workflows.py`** — the CI config still runs what it claims to.
- **`test_schema_ddl.py`** — the models still render every foreign key they declare
  on SQLite. `create_all` against an ALTER-capable dialect suppresses the two tables'
  cycle-breaking constraints *on the shared metadata*, permanently and process-wide,
  so a later SQLite `create_all` loses them. This is the tripwire; the leak and the
  fixture that undoes it are in `tests/integration/postgres/conftest.py`.
- **`test_coverage_floors.py`** — coverage did not rot, anywhere. The aggregate
  ratcheted in both directions, a floor every module clears on its own, and a capped
  debt list for the ones that do not yet. Reads `coverage.json`, so it carries the
  `coverage_gate` marker and only runs in `./scripts/test.sh coverage`, after the
  measured pass — a gate collected into the run it judges could only ever read the
  previous run's numbers.
- **`test_route_dependencies.py`** — every route still carries the auth dependency its
  sibling routes carry.
- **`test_rate_limiter_isolation.py`** — the suite can still find and reset every
  rate limiter. It reaches through FastAPI's internal route shapes, so an upgrade that
  changes them would otherwise turn the reset into a silent no-op and bring back an
  order-dependent flake with no failing test to point at.

A test belongs here when what it defends is a rule about the code rather than something a
user can observe.
