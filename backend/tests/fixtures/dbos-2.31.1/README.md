# DBOS 2.31.1 system-state snapshots

Generated with the real Python DBOS 2.31.1 SDK on SQLite and PostgreSQL 16.
Both snapshots contain a completed `printstash.job` (`dbos-231-result:1`)
and an enqueued `printstash.job` (`dbos-231-job:1`) on `derive.light`.
They use application version `contract-sdk-upgrade` and contain only fake data.

These SQL dumps preserve the old SDK's schema and serialized inputs/results.
Contract tests load them and launch the current adapter, exercising the SDK's
actual migration without downloading an old dependency during CI.

To regenerate, run `tests/fixtures/dbos-2.31.1/generate.py` from `backend/`
using a separate environment with `dbos==2.31.1`, `testcontainers[postgres]`
and `psycopg[binary]` installed, with Docker running. Do not regenerate them
with the current SDK: that would remove the upgrade boundary being tested.
UUIDs and timestamps vary between generations; the asserted IDs and values do not.
