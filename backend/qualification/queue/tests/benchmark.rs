use std::{
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
};

use printstash_queue_qualification::{
    adapters::azums_sqlite_contention,
    benchmark::{run_candidate, BenchmarkConfig, BenchmarkStore},
    Candidate,
};

struct TestDatabase(PathBuf);

static NEXT_DATABASE: AtomicU64 = AtomicU64::new(0);

impl TestDatabase {
    fn new() -> Self {
        let sequence = NEXT_DATABASE.fetch_add(1, Ordering::Relaxed);
        Self(std::env::temp_dir().join(format!(
            "printstash-queue-benchmark-{}-{sequence}.sqlite",
            std::process::id(),
        )))
    }
}

impl Drop for TestDatabase {
    fn drop(&mut self) {
        for suffix in ["", "-shm", "-wal"] {
            let _ = std::fs::remove_file(format!("{}{suffix}", self.0.display()));
        }
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn azums_benchmark_records_verified_file_backed_work() {
    let database = TestDatabase::new();
    let report = run_candidate(
        Candidate::Azums,
        BenchmarkStore::Sqlite(&database.0),
        BenchmarkConfig {
            jobs: 16,
            workers: 1,
            idle_seconds: 1,
            include_recovery: true,
        },
    )
    .await
    .unwrap();

    assert_eq!(report.candidate, Candidate::Azums);
    assert_eq!(report.accepted_count, 16);
    assert_eq!(report.completed_count, 16);
    assert_eq!(report.duplicate_executions, 0);
    assert_eq!(report.workers, 1);
    assert_eq!(report.enqueue.count, 16);
    assert_eq!(report.enqueue_to_start.count, 16);
    assert_eq!(report.acknowledgment.count, 16);
    assert_eq!(report.enqueue_to_terminal.count, 16);
    assert!(report.throughput_jobs_per_second > 0.0);
    assert!(report.idle_seconds >= 1.0);
    assert!(report.idle_cpu_seconds.is_finite());
    assert!(report.idle_cpu_seconds >= 0.0);
    assert!(report.recovery_seconds.is_some_and(|value| value >= 1.0));
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn azums_sqlite_contention_reports_unhandled_busy_error() {
    let database = TestDatabase::new();
    let observation = azums_sqlite_contention(&database.0).await.unwrap();

    assert_eq!(
        observation.contract,
        printstash_queue_qualification::Contract::OperateUnderContention
    );
    assert!(!observation.meets_contract, "{observation:#?}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn apalis_benchmark_records_verified_file_backed_work() {
    let database = TestDatabase::new();
    let report = run_candidate(
        Candidate::Apalis,
        BenchmarkStore::Sqlite(&database.0),
        BenchmarkConfig {
            jobs: 16,
            workers: 4,
            idle_seconds: 1,
            include_recovery: true,
        },
    )
    .await
    .unwrap();

    assert_eq!(report.candidate, Candidate::Apalis);
    assert_eq!(report.accepted_count, 16);
    assert_eq!(report.completed_count, 16);
    assert_eq!(report.duplicate_executions, 0);
    assert_eq!(report.enqueue.count, 16);
    assert_eq!(report.enqueue_to_start.count, 16);
    assert_eq!(report.acknowledgment.count, 16);
    assert_eq!(report.enqueue_to_terminal.count, 16);
    assert!(report.throughput_jobs_per_second > 0.0);
    assert!(report.idle_seconds >= 1.0);
    assert!(report.idle_cpu_seconds.is_finite());
    assert!(report.idle_cpu_seconds >= 0.0);
    assert!(report.recovery_seconds.is_some_and(|value| value > 0.0));
}

#[tokio::test]
async fn azums_benchmark_rejects_unbounded_work_before_migrations() {
    let database = TestDatabase::new();
    let error = run_candidate(
        Candidate::Azums,
        BenchmarkStore::Sqlite(&database.0),
        BenchmarkConfig {
            jobs: 0,
            workers: 1,
            idle_seconds: 1,
            include_recovery: false,
        },
    )
    .await
    .unwrap_err();

    assert!(error.to_string().contains("jobs must be between"));
}
