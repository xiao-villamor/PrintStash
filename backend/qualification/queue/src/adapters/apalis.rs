use std::{
    io::{self, Write as _},
    path::Path,
    process::Stdio,
    sync::Arc,
    time::Duration,
};

use anyhow::ensure;
use apalis::prelude::*;
use apalis_sql::{postgres::PostgresStorage, sqlite::SqliteStorage, Config};
use serde::{Deserialize, Serialize};
use serde_json::json;
use sqlx::{postgres::PgPoolOptions, sqlite::SqlitePoolOptions};
use tokio::{
    io::{AsyncBufReadExt as _, BufReader},
    process::Command,
    sync::Notify,
};

use crate::{Candidate, Contract, Database, Observation};

#[derive(Clone, Debug, Deserialize, Serialize)]
struct ProbeJob {
    value: u8,
}

#[derive(Default)]
struct Gate {
    started: Notify,
    release: Notify,
}

async fn old_handler(_job: ProbeJob, gate: Data<Arc<Gate>>) -> &'static str {
    gate.started.notify_one();
    gate.release.notified().await;
    "old-completion"
}

async fn new_handler(_job: ProbeJob, gate: Data<Arc<Gate>>) -> &'static str {
    gate.started.notify_one();
    gate.release.notified().await;
    "new-completion"
}

async fn process_blocking_handler(_job: ProbeJob) -> &'static str {
    println!("qualification-worker-started");
    io::stdout().flush().expect("stdout should remain writable");
    std::future::pending().await
}

async fn process_recovery_handler(_job: ProbeJob, started: Data<Arc<Notify>>) -> &'static str {
    started.notify_one();
    "recovered"
}

pub async fn run_postgres_blocking_worker(
    url: &str,
    namespace: &str,
    worker_id: &str,
) -> anyhow::Result<()> {
    let pool = PgPoolOptions::new().max_connections(4).connect(url).await?;
    PostgresStorage::<()>::setup(&pool).await?;
    let config = Config::new(namespace)
        .set_buffer_size(1)
        .set_poll_interval(Duration::from_millis(20))
        .set_keep_alive(Duration::from_millis(100))
        .set_reenqueue_orphaned_after(Duration::from_secs(1));
    WorkerBuilder::new(worker_id)
        .backend(PostgresStorage::new_with_config(pool, config))
        .build_fn(process_blocking_handler)
        .run()
        .await;
    Ok(())
}

pub async fn postgres_process_recovery(
    url: &str,
    executable: &Path,
) -> anyhow::Result<Observation> {
    let pool = PgPoolOptions::new().max_connections(8).connect(url).await?;
    PostgresStorage::<()>::setup(&pool).await?;
    let namespace = format!(
        "printstash-m01-apalis-process-recovery-{}",
        uuid::Uuid::new_v4()
    );
    let terminated_worker = format!("terminated-worker-{}", uuid::Uuid::new_v4());
    let successor_worker = format!("successor-worker-{}", uuid::Uuid::new_v4());
    let config = Config::new(&namespace)
        .set_buffer_size(1)
        .set_poll_interval(Duration::from_millis(20))
        .set_keep_alive(Duration::from_millis(100))
        .set_reenqueue_orphaned_after(Duration::from_secs(1));
    let mut control = PostgresStorage::new_with_config(pool.clone(), config.clone());
    let parts = control.push(ProbeJob { value: 1 }).await?;

    let mut child = Command::new(executable)
        .env(
            "PRINTSTASH_QUEUE_QUALIFICATION_MODE",
            "apalis-postgres-blocking-worker",
        )
        .env("PRINTSTASH_QUEUE_QUALIFICATION_POSTGRES_URL", url)
        .env("PRINTSTASH_QUEUE_QUALIFICATION_NAMESPACE", &namespace)
        .env(
            "PRINTSTASH_QUEUE_QUALIFICATION_WORKER_ID",
            &terminated_worker,
        )
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| anyhow::anyhow!("qualification worker stdout was not piped"))?;
    let mut lines = BufReader::new(stdout).lines();
    let started_line = tokio::time::timeout(Duration::from_secs(5), lines.next_line()).await??;
    ensure!(started_line.as_deref() == Some("qualification-worker-started"));
    child.kill().await?;
    let status = child.wait().await?;

    let successor_started = Arc::new(Notify::new());
    let successor = WorkerBuilder::new(successor_worker)
        .data(Arc::clone(&successor_started))
        .backend(PostgresStorage::new_with_config(pool.clone(), config))
        .build_fn(process_recovery_handler);
    let running = successor.run();
    let handle = running.get_handle();
    let task = tokio::spawn(running);
    let recovered = tokio::time::timeout(Duration::from_secs(5), successor_started.notified())
        .await
        .is_ok();
    let durable: (String, Option<String>) =
        sqlx::query_as("SELECT status, lock_by FROM apalis.jobs WHERE id = $1")
            .bind(parts.task_id.to_string())
            .fetch_one(&pool)
            .await?;
    handle.stop();
    let _ = tokio::time::timeout(Duration::from_secs(2), task).await;
    pool.close().await;

    Ok(Observation::new(
        Candidate::Apalis,
        Database::Postgres,
        Contract::RecoverInterruptedWork,
        recovered,
        "a job owned by a terminated worker process becomes runnable within five seconds",
        json!({
            "successor_started": recovered,
            "terminated_worker_success": status.success(),
            "durable": durable,
        }),
    ))
}

pub async fn postgres_stale_completion(url: &str) -> anyhow::Result<Observation> {
    let pool = PgPoolOptions::new().max_connections(8).connect(url).await?;
    PostgresStorage::<()>::setup(&pool).await?;
    let namespace = "printstash-m01-apalis-stale";
    let successor_config = Config::new(namespace)
        .set_buffer_size(1)
        .set_poll_interval(Duration::from_millis(20))
        .set_keep_alive(Duration::from_secs(30));
    let old_config = Config::new(namespace)
        .set_buffer_size(1)
        .set_poll_interval(Duration::from_secs(5))
        .set_keep_alive(Duration::from_secs(30));
    let mut control = PostgresStorage::new_with_config(pool.clone(), successor_config.clone());
    let parts = control.push(ProbeJob { value: 1 }).await?;

    let old_gate = Arc::new(Gate::default());
    let old_worker = WorkerBuilder::new("old-worker")
        .data(old_gate.clone())
        .backend(PostgresStorage::new_with_config(pool.clone(), old_config))
        .build_fn(old_handler);
    let old_run = old_worker.run();
    let old_handle = old_run.get_handle();
    let old_task = tokio::spawn(old_run);
    tokio::time::timeout(Duration::from_secs(8), old_gate.started.notified()).await?;

    control
        .retry(&WorkerId::new("old-worker"), &parts.task_id)
        .await?;
    let new_gate = Arc::new(Gate::default());
    let new_worker = WorkerBuilder::new("new-worker")
        .data(new_gate.clone())
        .backend(PostgresStorage::new_with_config(
            pool.clone(),
            successor_config,
        ))
        .build_fn(new_handler);
    let new_run = new_worker.run();
    let new_handle = new_run.get_handle();
    let new_task = tokio::spawn(new_run);
    tokio::time::timeout(Duration::from_secs(5), new_gate.started.notified()).await?;

    let before: (String, Option<String>) =
        sqlx::query_as("SELECT status, lock_by FROM apalis.jobs WHERE id = $1")
            .bind(parts.task_id.to_string())
            .fetch_one(&pool)
            .await?;
    ensure!(before == ("Running".to_owned(), Some("new-worker".to_owned())));
    old_gate.release.notify_one();

    let mut after = before.clone();
    for _ in 0..100 {
        after = sqlx::query_as("SELECT status, lock_by FROM apalis.jobs WHERE id = $1")
            .bind(parts.task_id.to_string())
            .fetch_one(&pool)
            .await?;
        if after != before {
            break;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }

    new_gate.release.notify_one();
    tokio::time::sleep(Duration::from_millis(100)).await;
    old_handle.stop();
    new_handle.stop();
    let _ = tokio::time::timeout(Duration::from_secs(2), old_task).await;
    let _ = tokio::time::timeout(Duration::from_secs(2), new_task).await;
    pool.close().await;

    Ok(Observation::new(
        Candidate::Apalis,
        Database::Postgres,
        Contract::RejectStaleCompletion,
        after == before,
        "the delayed old acknowledgement leaves the successor claim unchanged",
        json!({"before": before, "after_old_ack": after}),
    ))
}

pub async fn postgres_atomic_acceptance(url: &str) -> anyhow::Result<Observation> {
    let pool = PgPoolOptions::new().max_connections(4).connect(url).await?;
    PostgresStorage::<()>::setup(&pool).await?;
    sqlx::query("CREATE TABLE qualification_app_state (id TEXT PRIMARY KEY)")
        .execute(&pool)
        .await?;
    let namespace = "printstash-m01-apalis-atomicity";
    let mut storage = PostgresStorage::new_with_config(pool.clone(), Config::new(namespace));
    storage.push(ProbeJob { value: 1 }).await?;

    let mut application = pool.begin().await?;
    sqlx::query("INSERT INTO qualification_app_state (id) VALUES ($1)")
        .bind("rolled-back")
        .execute(&mut *application)
        .await?;
    application.rollback().await?;
    let application_rows: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM qualification_app_state")
        .fetch_one(&pool)
        .await?;
    let queue_rows: i64 =
        sqlx::query_scalar("SELECT COUNT(*) FROM apalis.jobs WHERE job_type = $1")
            .bind(namespace)
            .fetch_one(&pool)
            .await?;
    pool.close().await;

    Ok(Observation::new(
        Candidate::Apalis,
        Database::Postgres,
        Contract::AtomicAcceptanceRollback,
        application_rows == 0 && queue_rows == 0,
        "application rollback leaves no executable job through the supported enqueue API",
        json!({"application_rows": application_rows, "queue_rows": queue_rows}),
    ))
}

async fn gated_handler(_job: ProbeJob, gate: Data<Arc<Gate>>) -> &'static str {
    gate.started.notify_one();
    gate.release.notified().await;
    "completion"
}

async fn sqlite_successor_completion(
    path: &Path,
    successor_id: &'static str,
    contract: Contract,
    expected: &'static str,
) -> anyhow::Result<Observation> {
    let url = format!("sqlite://{}?mode=rwc", path.display());
    let pool = SqlitePoolOptions::new()
        .max_connections(8)
        .connect(&url)
        .await?;
    SqliteStorage::<()>::setup(&pool).await?;
    let config = Config::new("printstash-m01-apalis-successor")
        .set_buffer_size(1)
        .set_poll_interval(Duration::from_millis(20))
        .set_keep_alive(Duration::from_secs(30));
    let mut control = SqliteStorage::new_with_config(pool.clone(), config.clone());
    let parts = control.push(ProbeJob { value: 1 }).await?;

    let old_gate = Arc::new(Gate::default());
    let old_worker = WorkerBuilder::new("old-worker")
        .data(old_gate.clone())
        .backend(SqliteStorage::new_with_config(pool.clone(), config.clone()))
        .build_fn(gated_handler);
    let old_run = old_worker.run();
    let old_handle = old_run.get_handle();
    let old_task = tokio::spawn(old_run);
    tokio::time::timeout(Duration::from_secs(5), old_gate.started.notified()).await?;
    old_handle.stop();

    control
        .retry(&WorkerId::new("old-worker"), &parts.task_id)
        .await?;
    let new_gate = Arc::new(Gate::default());
    let new_worker = WorkerBuilder::new(successor_id)
        .data(new_gate.clone())
        .backend(SqliteStorage::new_with_config(pool.clone(), config))
        .build_fn(gated_handler);
    let new_run = new_worker.run();
    let new_handle = new_run.get_handle();
    let new_task = tokio::spawn(new_run);
    tokio::time::timeout(Duration::from_secs(5), new_gate.started.notified()).await?;

    let before: (String, Option<String>) =
        sqlx::query_as("SELECT status, lock_by FROM Jobs WHERE id = ?")
            .bind(parts.task_id.to_string())
            .fetch_one(&pool)
            .await?;
    old_gate.release.notify_one();
    tokio::time::timeout(Duration::from_secs(2), old_task).await??;
    let after: (String, Option<String>) =
        sqlx::query_as("SELECT status, lock_by FROM Jobs WHERE id = ?")
            .bind(parts.task_id.to_string())
            .fetch_one(&pool)
            .await?;

    new_gate.release.notify_one();
    tokio::time::sleep(Duration::from_millis(100)).await;
    new_handle.stop();
    let _ = tokio::time::timeout(Duration::from_secs(2), new_task).await;
    pool.close().await;

    Ok(Observation::new(
        Candidate::Apalis,
        Database::Sqlite,
        contract,
        after == before,
        expected,
        json!({"before": before, "after_old_ack": after}),
    ))
}

pub async fn sqlite_distinct_worker_completion(path: &Path) -> anyhow::Result<Observation> {
    sqlite_successor_completion(
        path,
        "new-worker",
        Contract::RejectStaleCompletion,
        "the delayed old acknowledgement leaves the distinct successor claim unchanged",
    )
    .await
}

pub async fn sqlite_reused_worker_completion(path: &Path) -> anyhow::Result<Observation> {
    sqlite_successor_completion(
        path,
        "old-worker",
        Contract::RejectReusedWorkerStaleCompletion,
        "the delayed first attempt cannot acknowledge a successor with a reused worker ID",
    )
    .await
}

pub async fn sqlite_atomic_acceptance(path: &Path) -> anyhow::Result<Observation> {
    let url = format!("sqlite://{}?mode=rwc", path.display());
    let pool = SqlitePoolOptions::new()
        .max_connections(4)
        .connect(&url)
        .await?;
    SqliteStorage::<()>::setup(&pool).await?;
    sqlx::query("CREATE TABLE qualification_app_state (id TEXT PRIMARY KEY)")
        .execute(&pool)
        .await?;
    let namespace = "printstash-m01-apalis-atomicity";
    let mut storage = SqliteStorage::new_with_config(pool.clone(), Config::new(namespace));
    storage.push(ProbeJob { value: 1 }).await?;

    let mut application = pool.begin().await?;
    sqlx::query("INSERT INTO qualification_app_state (id) VALUES (?)")
        .bind("rolled-back")
        .execute(&mut *application)
        .await?;
    application.rollback().await?;
    let application_rows: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM qualification_app_state")
        .fetch_one(&pool)
        .await?;
    let queue_rows: i64 = sqlx::query_scalar("SELECT COUNT(*) FROM Jobs WHERE job_type = ?")
        .bind(namespace)
        .fetch_one(&pool)
        .await?;
    pool.close().await;

    Ok(Observation::new(
        Candidate::Apalis,
        Database::Sqlite,
        Contract::AtomicAcceptanceRollback,
        application_rows == 0 && queue_rows == 0,
        "application rollback leaves no executable job through the supported enqueue API",
        json!({"application_rows": application_rows, "queue_rows": queue_rows}),
    ))
}

async fn fail_once(_job: ProbeJob, started: Data<Arc<Notify>>) -> Result<(), io::Error> {
    started.notify_one();
    Err(io::Error::other("qualification failure"))
}

async fn succeed(_job: ProbeJob, started: Data<Arc<Notify>>) {
    started.notify_one();
}

pub async fn sqlite_failed_recovery(path: &Path) -> anyhow::Result<Observation> {
    let url = format!("sqlite://{}?mode=rwc", path.display());
    let pool = SqlitePoolOptions::new()
        .max_connections(8)
        .connect(&url)
        .await?;
    SqliteStorage::<()>::setup(&pool).await?;
    let config = Config::new("printstash-m01-apalis-failed-recovery")
        .set_buffer_size(1)
        .set_poll_interval(Duration::from_millis(500))
        .set_keep_alive(Duration::from_secs(30));
    let mut control = SqliteStorage::new_with_config(pool.clone(), config.clone());
    let parts = control.push(ProbeJob { value: 1 }).await?;

    let failed = Arc::new(Notify::new());
    let old_worker = WorkerBuilder::new("failed-worker")
        .data(failed.clone())
        .backend(SqliteStorage::new_with_config(pool.clone(), config.clone()))
        .build_fn(fail_once);
    let old_run = old_worker.run();
    let old_handle = old_run.get_handle();
    let old_task = tokio::spawn(old_run);
    tokio::time::timeout(Duration::from_secs(5), failed.notified()).await?;
    let mut failed_status = String::new();
    for _ in 0..20 {
        failed_status = sqlx::query_scalar("SELECT status FROM Jobs WHERE id = ?")
            .bind(parts.task_id.to_string())
            .fetch_one(&pool)
            .await?;
        if failed_status == "Failed" {
            break;
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    ensure!(
        failed_status == "Failed",
        "failure acknowledgement did not persist"
    );
    old_handle.stop();
    tokio::time::timeout(Duration::from_secs(2), old_task).await??;

    let recovered = Arc::new(Notify::new());
    let new_worker = WorkerBuilder::new("recovery-worker")
        .data(recovered.clone())
        .backend(SqliteStorage::new_with_config(pool.clone(), config))
        .build_fn(succeed);
    let new_run = new_worker.run();
    let new_handle = new_run.get_handle();
    let new_task = tokio::spawn(new_run);
    let successor_started = tokio::time::timeout(Duration::from_millis(1500), recovered.notified())
        .await
        .is_ok();
    let row: (String, Option<String>, i64) =
        sqlx::query_as("SELECT status, lock_by, attempts FROM Jobs WHERE id = ?")
            .bind(parts.task_id.to_string())
            .fetch_one(&pool)
            .await?;

    new_handle.stop();
    let _ = tokio::time::timeout(Duration::from_secs(2), new_task).await;
    pool.close().await;

    Ok(Observation::new(
        Candidate::Apalis,
        Database::Sqlite,
        Contract::RetryFailedWork,
        successor_started,
        "a fresh worker starts the retryable failed job",
        json!({"successor_started": successor_started, "durable": row}),
    ))
}
