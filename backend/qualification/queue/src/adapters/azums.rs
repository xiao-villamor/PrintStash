use std::{path::Path, time::Duration};

use anyhow::ensure;
use azums::{make_pool, make_sqlite_pool, Job, PostgresBackend, SqliteBackend, StorageBackend};
use serde_json::json;

use crate::{Candidate, Contract, Database, Observation};

async fn expired_ownership<B: StorageBackend>(
    backend: &B,
    database: Database,
) -> anyhow::Result<Vec<Observation>> {
    backend.run_migrations().await?;
    let completion_job = backend
        .enqueue(
            Job::new("expired-completion", json!({}))
                .queue("expiry")
                .into(),
        )
        .await?;
    let leased = backend.lease_jobs_batch("expiry", "worker-a", 1, 1).await?;
    ensure!(leased.len() == 1 && leased[0].id == completion_job);
    let attempts = backend
        .start_attempts_batch(
            &[leased[0].dataset_id.clone()],
            &[completion_job],
            "worker-a",
        )
        .await?;
    tokio::time::sleep(Duration::from_millis(1500)).await;
    let completion = backend
        .mark_succeeded(completion_job, attempts[0].1, "worker-a", 1)
        .await;
    let completion_status = backend.get_job(completion_job).await?.map(|job| job.status);
    let completion_accepted = completion.is_ok();

    let heartbeat_job = backend
        .enqueue(
            Job::new("expired-heartbeat", json!({}))
                .queue("heartbeat")
                .into(),
        )
        .await?;
    let heartbeat_lease = backend
        .lease_jobs_batch("heartbeat", "worker-b", 1, 1)
        .await?;
    ensure!(heartbeat_lease.len() == 1 && heartbeat_lease[0].id == heartbeat_job);
    tokio::time::sleep(Duration::from_millis(1500)).await;
    let heartbeat_accepted = backend.extend_lease(heartbeat_job, "worker-b", 30).await?;

    Ok(vec![
        Observation::new(
            Candidate::Azums,
            database,
            Contract::RejectCompletionAfterExpiry,
            !completion_accepted,
            "completion after the lease deadline is refused before reaping",
            json!({
                "completion_accepted": completion_accepted,
                "durable_status": completion_status,
            }),
        ),
        Observation::new(
            Candidate::Azums,
            database,
            Contract::RejectRenewalAfterExpiry,
            !heartbeat_accepted,
            "renewal after the lease deadline is refused before reaping",
            json!({"renewal_accepted": heartbeat_accepted}),
        ),
    ])
}

async fn successor_fencing<B: StorageBackend>(
    backend: &B,
    database: Database,
) -> anyhow::Result<Vec<Observation>> {
    backend.run_migrations().await?;
    let mut observations = Vec::new();
    for (queue, successor, contract) in [
        (
            "distinct-successor",
            "worker-new",
            Contract::RejectStaleCompletion,
        ),
        (
            "reused-successor",
            "worker-old",
            Contract::RejectReusedWorkerStaleCompletion,
        ),
    ] {
        let job_id = backend
            .enqueue(Job::new(queue, json!({})).queue(queue).into())
            .await?;
        let old_lease = backend.lease_jobs_batch(queue, "worker-old", 1, 1).await?;
        ensure!(old_lease.len() == 1 && old_lease[0].id == job_id);
        let old_attempt = backend
            .start_attempts_batch(&[old_lease[0].dataset_id.clone()], &[job_id], "worker-old")
            .await?;
        tokio::time::sleep(Duration::from_millis(1500)).await;
        ensure!(backend.reap_expired_locks().await? >= 1);

        let new_lease = backend.lease_jobs_batch(queue, successor, 30, 1).await?;
        ensure!(new_lease.len() == 1 && new_lease[0].id == job_id);
        let new_attempt = backend
            .start_attempts_batch(&[new_lease[0].dataset_id.clone()], &[job_id], successor)
            .await?;
        let stale_completion = backend
            .mark_succeeded(job_id, old_attempt[0].1, "worker-old", 1)
            .await;
        let current = backend
            .get_job(job_id)
            .await?
            .ok_or_else(|| anyhow::anyhow!("successor job disappeared"))?;
        let meets_contract = stale_completion.is_err()
            && current.status == "running"
            && current.locked_by.as_deref() == Some(successor);
        observations.push(Observation::new(
            Candidate::Azums,
            database,
            contract,
            meets_contract,
            "a delayed completion cannot change the successor's running attempt",
            json!({
                "stale_completion_accepted": stale_completion.is_ok(),
                "durable_status": current.status,
                "durable_owner": current.locked_by,
                "successor_reused_worker_id": successor == "worker-old",
            }),
        ));
        backend
            .mark_succeeded(job_id, new_attempt[0].1, successor, 1)
            .await?;
    }
    Ok(observations)
}

pub async fn sqlite_expired_ownership(path: &Path) -> anyhow::Result<Vec<Observation>> {
    let url = format!("sqlite://{}?mode=rwc", path.display());
    let pool = make_sqlite_pool(&url).await?;
    let backend = SqliteBackend::new(pool.clone());
    let observations = expired_ownership(&backend, Database::Sqlite).await;
    pool.close().await;
    observations
}

pub async fn postgres_expired_ownership(url: &str) -> anyhow::Result<Vec<Observation>> {
    let pool = make_pool(url).await?;
    let backend = PostgresBackend::new(pool.clone());
    let observations = expired_ownership(&backend, Database::Postgres).await;
    pool.close().await;
    observations
}

pub async fn sqlite_successor_fencing(path: &Path) -> anyhow::Result<Vec<Observation>> {
    let url = format!("sqlite://{}?mode=rwc", path.display());
    let pool = make_sqlite_pool(&url).await?;
    let backend = SqliteBackend::new(pool.clone());
    let observations = successor_fencing(&backend, Database::Sqlite).await;
    pool.close().await;
    observations
}

pub async fn postgres_successor_fencing(url: &str) -> anyhow::Result<Vec<Observation>> {
    let pool = make_pool(url).await?;
    let backend = PostgresBackend::new(pool.clone());
    let observations = successor_fencing(&backend, Database::Postgres).await;
    pool.close().await;
    observations
}

pub async fn sqlite_atomic_acceptance(path: &Path) -> anyhow::Result<Vec<Observation>> {
    let url = format!("sqlite://{}?mode=rwc", path.display());
    let pool = make_sqlite_pool(&url).await?;
    let backend = SqliteBackend::new(pool.clone());
    backend.run_migrations().await?;
    sqlx::query("CREATE TABLE qualification_app_state (id TEXT PRIMARY KEY)")
        .execute(&pool)
        .await?;

    let mut committed = pool.begin().await?;
    sqlx::query("INSERT INTO qualification_app_state (id) VALUES (?)")
        .bind("committed")
        .execute(&mut *committed)
        .await?;
    backend
        .enqueue_in_tx(
            &mut committed,
            Job::new("qualification-committed", json!({})).into(),
        )
        .await?;
    committed.commit().await?;

    let mut rolled_back = pool.begin().await?;
    sqlx::query("INSERT INTO qualification_app_state (id) VALUES (?)")
        .bind("rolled-back")
        .execute(&mut *rolled_back)
        .await?;
    backend
        .enqueue_in_tx(
            &mut rolled_back,
            Job::new("qualification-rolled-back", json!({})).into(),
        )
        .await?;
    rolled_back.rollback().await?;

    let committed_counts = (
        sqlx::query_scalar("SELECT COUNT(*) FROM qualification_app_state WHERE id = ?")
            .bind("committed")
            .fetch_one(&pool)
            .await?,
        sqlx::query_scalar("SELECT COUNT(*) FROM jobs WHERE job_type = ?")
            .bind("qualification-committed")
            .fetch_one(&pool)
            .await?,
    );
    let rolled_back_counts = (
        sqlx::query_scalar("SELECT COUNT(*) FROM qualification_app_state WHERE id = ?")
            .bind("rolled-back")
            .fetch_one(&pool)
            .await?,
        sqlx::query_scalar("SELECT COUNT(*) FROM jobs WHERE job_type = ?")
            .bind("qualification-rolled-back")
            .fetch_one(&pool)
            .await?,
    );
    pool.close().await;

    Ok(atomic_observations(
        Database::Sqlite,
        committed_counts,
        rolled_back_counts,
    ))
}

pub async fn postgres_atomic_acceptance(url: &str) -> anyhow::Result<Vec<Observation>> {
    let pool = make_pool(url).await?;
    let backend = PostgresBackend::new(pool.clone());
    backend.run_migrations().await?;
    sqlx::query("CREATE TABLE qualification_app_state (id TEXT PRIMARY KEY)")
        .execute(&pool)
        .await?;

    let mut committed = pool.begin().await?;
    sqlx::query("INSERT INTO qualification_app_state (id) VALUES ($1)")
        .bind("committed")
        .execute(&mut *committed)
        .await?;
    backend
        .enqueue_in_tx(
            &mut committed,
            Job::new("qualification-committed", json!({})).into(),
        )
        .await?;
    committed.commit().await?;

    let mut rolled_back = pool.begin().await?;
    sqlx::query("INSERT INTO qualification_app_state (id) VALUES ($1)")
        .bind("rolled-back")
        .execute(&mut *rolled_back)
        .await?;
    backend
        .enqueue_in_tx(
            &mut rolled_back,
            Job::new("qualification-rolled-back", json!({})).into(),
        )
        .await?;
    rolled_back.rollback().await?;

    let committed_counts = (
        sqlx::query_scalar("SELECT COUNT(*) FROM qualification_app_state WHERE id = $1")
            .bind("committed")
            .fetch_one(&pool)
            .await?,
        sqlx::query_scalar("SELECT COUNT(*) FROM jobs WHERE job_type = $1")
            .bind("qualification-committed")
            .fetch_one(&pool)
            .await?,
    );
    let rolled_back_counts = (
        sqlx::query_scalar("SELECT COUNT(*) FROM qualification_app_state WHERE id = $1")
            .bind("rolled-back")
            .fetch_one(&pool)
            .await?,
        sqlx::query_scalar("SELECT COUNT(*) FROM jobs WHERE job_type = $1")
            .bind("qualification-rolled-back")
            .fetch_one(&pool)
            .await?,
    );
    pool.close().await;

    Ok(atomic_observations(
        Database::Postgres,
        committed_counts,
        rolled_back_counts,
    ))
}

fn atomic_observations(
    database: Database,
    committed_counts: (i64, i64),
    rolled_back_counts: (i64, i64),
) -> Vec<Observation> {
    vec![
        Observation::new(
            Candidate::Azums,
            database,
            Contract::AtomicAcceptanceCommit,
            committed_counts == (1, 1),
            "application state and durable job become visible together",
            json!({"application_rows": committed_counts.0, "job_rows": committed_counts.1}),
        ),
        Observation::new(
            Candidate::Azums,
            database,
            Contract::AtomicAcceptanceRollback,
            rolled_back_counts == (0, 0),
            "application rollback leaves no executable job",
            json!({"application_rows": rolled_back_counts.0, "job_rows": rolled_back_counts.1}),
        ),
    ]
}

pub async fn sqlite_contention(path: &Path) -> anyhow::Result<Observation> {
    let url = format!("sqlite://{}?mode=rwc", path.display());
    let pool = make_sqlite_pool(&url).await?;
    let backend = SqliteBackend::new(pool.clone());
    backend.run_migrations().await?;
    let job_id = backend
        .enqueue(Job::new("qualification-contention", json!({})).into())
        .await?;
    let leased = backend
        .lease_jobs_batch("default", "contended-worker", 30, 1)
        .await?;
    ensure!(leased.len() == 1 && leased[0].id == job_id);

    let mut blocker = pool.begin().await?;
    sqlx::query("UPDATE jobs SET priority = priority WHERE id = ?")
        .bind(job_id)
        .execute(&mut *blocker)
        .await?;
    let contender = backend.clone();
    let dataset_id = leased[0].dataset_id.clone();
    let attempt = tokio::spawn(async move {
        contender
            .start_attempts_batch(&[dataset_id], &[job_id], "contended-worker")
            .await
    });
    tokio::time::sleep(Duration::from_millis(100)).await;
    blocker.commit().await?;
    let result = attempt.await?;
    let error_category = result
        .as_ref()
        .err()
        .map(|error| {
            let chain = format!("{error:#}");
            if chain.contains("database is locked") {
                "sqlite_busy"
            } else {
                "storage_error"
            }
        })
        .unwrap_or("none");
    pool.close().await;

    Ok(Observation::new(
        Candidate::Azums,
        Database::Sqlite,
        Contract::OperateUnderContention,
        result.is_ok(),
        "attempt startup handles a concurrent SQLite writer through the supported library path",
        json!({"operation_succeeded": result.is_ok(), "error_category": error_category}),
    ))
}
