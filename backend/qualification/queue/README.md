# Queue qualification harness

This non-publishable crate evaluates durable queue releases without adding them
to PrintStash's production dependency graph. It uses each candidate's public
enqueue, worker, lease, acknowledgement, retry, heartbeat, and reaper APIs.
Direct SQL is limited to observing durable state and constructing a concurrent
writer fault boundary; it does not replace candidate queue behavior.

The default test set uses real file-backed SQLite. PostgreSQL tests require two
fresh, isolated databases because Apalis and Azums both own migrations in
SQLx's default migration-history table:

```console
PRINTSTASH_QUEUE_QUALIFICATION_APALIS_POSTGRES_URL=... \
PRINTSTASH_QUEUE_QUALIFICATION_AZUMS_POSTGRES_URL=... \
cargo +1.98.1 test --locked --features postgres-tests -- --test-threads=1
```

Expected candidate failures are assertions. If a pinned upstream release starts
passing one, the test fails so the adoption decision must be reviewed rather
than silently preserving an obsolete rejection.

Steady Apalis measurements use a 30-second orphan threshold so acknowledgement
batching cannot turn timing work into artificial retries. The separate recovery
scenario uses a one-second threshold and reports its recovery time explicitly.

Production imports remain coordinated by Python and durably owned by
`BackgroundJob` throughout M01. This crate never runs in application images.
