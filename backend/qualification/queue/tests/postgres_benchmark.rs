#![cfg(feature = "postgres-tests")]

use printstash_queue_qualification::{
    benchmark::{run_candidate, BenchmarkConfig, BenchmarkStore},
    Candidate, Database,
};

fn database_url(candidate: &str) -> String {
    let name = format!(
        "PRINTSTASH_QUEUE_QUALIFICATION_{}_POSTGRES_URL",
        candidate.to_ascii_uppercase()
    );
    std::env::var(&name).unwrap_or_else(|_| {
        panic!("postgres-tests requires {name} for a fresh real PostgreSQL database")
    })
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn apalis_benchmark_records_verified_postgres_work() {
    let url = database_url("apalis");
    let report = run_candidate(
        Candidate::Apalis,
        BenchmarkStore::Postgres(&url),
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
    assert_eq!(report.database, Database::Postgres);
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

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn azums_benchmark_records_verified_postgres_work() {
    let url = database_url("azums");
    let report = run_candidate(
        Candidate::Azums,
        BenchmarkStore::Postgres(&url),
        BenchmarkConfig {
            jobs: 16,
            workers: 4,
            idle_seconds: 1,
            include_recovery: true,
        },
    )
    .await
    .unwrap();

    assert_eq!(report.candidate, Candidate::Azums);
    assert_eq!(report.database, Database::Postgres);
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
    assert!(report.recovery_seconds.is_some_and(|value| value >= 1.0));
}
