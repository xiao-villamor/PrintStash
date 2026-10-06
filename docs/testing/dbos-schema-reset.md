# Reset only the disposable engine schema

The application database remains authoritative when a JobEngine resets. SQLite
uses a sibling engine file; PostgreSQL uses the `dbos` schema in the application
database. The SDK destructive reset drops the entire PostgreSQL database even
when supplied a schema, deleting unrelated application tables and preventing
FIFO startup checks from connecting on relaunch.

The adapter stops the engine, drops only its configured PostgreSQL schema using
SQLAlchemy transactional DDL, and propagates database errors. SQLite keeps its
existing SDK reset. The next launch recreates engine tables and its schedule;
FIFO admission still rejects undrained legacy work. No migration is changed.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | Preserves application data | Happy | Real PostgreSQL database with a public application row and DBOS schema | Application row survives reset and engine relaunch | Contract | ✅ `tests/contract/runtime/engine/test_dbos_engine.py::TestPostgresReset::test_preserves_application_data` |
| 2 | Reset recreates the schedule | Edge | Launched engine; SQLite / PostgreSQL | Relaunch installs exactly one durable reconciler schedule | Contract | ✅ `tests/contract/runtime/engine/test_dbos_engine.py::TestPersistentTick::test_reset_recreates_the_schedule` |
| 3 | Refuses PostgreSQL without a system schema | Error | PostgreSQL engine without a configured disposable schema | Reset raises before accessing the database | Unit | ✅ `tests/unit/runtime/engine/test_dbos_engine.py::TestReset::test_refuses_postgres_without_a_system_schema` |
| 4 | Refuses legacy backfill before consumption | Error | Pending legacy priority work on either derivation lane | FIFO startup raises; pending work remains intact and unconsumed | Contract | ✅ `tests/contract/runtime/engine/test_dbos_engine.py::TestQueueTransition::test_refuses_legacy_backfill_before_consumption` |

Real data preservation regression: **1 failed in 16.80s** before the repair.

Focused validation: **42 passed, 1 warning in 37.68s**. Ruff, formatting and whitespace checks pass. No local full, coverage or Deep CI was run.
