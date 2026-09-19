//! Candidate-specific qualification adapters.

mod apalis;
mod azums;

pub use apalis::{
    postgres_atomic_acceptance as apalis_postgres_atomic_acceptance, postgres_process_recovery,
    postgres_stale_completion, run_postgres_blocking_worker,
    sqlite_atomic_acceptance as apalis_sqlite_atomic_acceptance, sqlite_distinct_worker_completion,
    sqlite_failed_recovery, sqlite_reused_worker_completion,
};
pub use azums::{
    postgres_atomic_acceptance as azums_postgres_atomic_acceptance, postgres_expired_ownership,
    postgres_successor_fencing, sqlite_atomic_acceptance as azums_sqlite_atomic_acceptance,
    sqlite_contention as azums_sqlite_contention, sqlite_expired_ownership,
    sqlite_successor_fencing,
};
