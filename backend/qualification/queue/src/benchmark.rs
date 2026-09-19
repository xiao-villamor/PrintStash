//! Comparable queue measurements driven through candidate public APIs.

use std::{
    collections::{BTreeMap, HashMap, HashSet},
    path::Path,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    },
    time::{Duration, Instant},
};

use anyhow::{ensure, Context as _};
use apalis::prelude::*;
use apalis_sql::{context::SqlContext, postgres::PostgresStorage, sqlite::SqliteStorage, Config};
use azums::{make_pool, make_sqlite_pool, Job, PostgresBackend, SqliteBackend, StorageBackend};
use cpu_time::ProcessTime;
use serde::{Deserialize, Serialize};
use sqlx::{postgres::PgPoolOptions, sqlite::SqlitePoolOptions, SqlitePool};
use tokio::{sync::Mutex, task::JoinSet};
use uuid::Uuid;

use crate::{evidence::dependency_versions, evidence::enabled_features, Candidate, Database};

// Recovery uses an intentionally short lease in its isolated scenario. The steady
// benchmark leaves enough headroom for candidate acknowledgement batching.
const STEADY_REENQUEUE_AFTER: Duration = Duration::from_secs(30);

#[derive(Clone, Debug, Deserialize, Serialize)]
struct ApalisBenchmarkJob {
    sequence: usize,
}

#[derive(Default)]
struct ApalisBenchmarkState {
    accepted: Mutex<HashMap<usize, Instant>>,
    starts: Mutex<Vec<Duration>>,
    handled_at: Mutex<HashMap<usize, Instant>>,
    seen: Mutex<HashSet<usize>>,
    handled: AtomicUsize,
    handled_notify: tokio::sync::Notify,
}

async fn apalis_benchmark_handler(
    job: ApalisBenchmarkJob,
    state: Data<Arc<ApalisBenchmarkState>>,
) -> &'static str {
    if let Some(accepted) = state.accepted.lock().await.get(&job.sequence) {
        state.starts.lock().await.push(accepted.elapsed());
    }
    state.seen.lock().await.insert(job.sequence);
    state
        .handled_at
        .lock()
        .await
        .insert(job.sequence, Instant::now());
    state.handled.fetch_add(1, Ordering::Release);
    state.handled_notify.notify_one();
    "completed"
}

#[derive(Default)]
struct RecoveryGate {
    started: tokio::sync::Notify,
}

async fn recovery_handler(_job: ApalisBenchmarkJob, gate: Data<Arc<RecoveryGate>>) -> &'static str {
    gate.started.notify_one();
    std::future::pending().await
}

async fn recovery_successor(
    _job: ApalisBenchmarkJob,
    gate: Data<Arc<RecoveryGate>>,
) -> &'static str {
    gate.started.notify_one();
    "recovered"
}

#[derive(Clone, Debug, Serialize)]
pub struct LatencySummary {
    pub count: usize,
    pub p50_ms: f64,
    pub p95_ms: f64,
    pub max_ms: f64,
}

impl LatencySummary {
    fn from_durations(values: &[Duration]) -> anyhow::Result<Self> {
        ensure!(!values.is_empty(), "latency samples cannot be empty");
        let mut millis: Vec<f64> = values
            .iter()
            .map(|value| value.as_secs_f64() * 1_000.0)
            .collect();
        millis.sort_by(f64::total_cmp);
        Ok(Self {
            count: millis.len(),
            p50_ms: percentile(&millis, 0.50),
            p95_ms: percentile(&millis, 0.95),
            max_ms: *millis.last().expect("samples are non-empty"),
        })
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct CandidateBenchmark {
    pub schema_version: u8,
    pub measurement_protocol: &'static str,
    pub candidate: Candidate,
    pub database: Database,
    pub dependency_versions: BTreeMap<&'static str, &'static str>,
    pub enabled_features: Vec<&'static str>,
    pub jobs: usize,
    pub workers: usize,
    pub accepted_count: usize,
    pub completed_count: usize,
    pub duplicate_executions: usize,
    pub enqueue: LatencySummary,
    pub enqueue_to_start: LatencySummary,
    pub acknowledgment: LatencySummary,
    pub enqueue_to_terminal: LatencySummary,
    pub processing_seconds: f64,
    pub throughput_jobs_per_second: f64,
    pub idle_seconds: f64,
    pub idle_cpu_seconds: f64,
    pub recovery_seconds: Option<f64>,
}

#[derive(Clone, Copy, Debug)]
pub struct BenchmarkConfig {
    pub jobs: usize,
    pub workers: usize,
    pub idle_seconds: u64,
    pub include_recovery: bool,
}

#[derive(Clone, Copy, Debug)]
pub enum BenchmarkStore<'a> {
    Sqlite(&'a Path),
    Postgres(&'a str),
}

pub async fn run_candidate(
    candidate: Candidate,
    store: BenchmarkStore<'_>,
    config: BenchmarkConfig,
) -> anyhow::Result<CandidateBenchmark> {
    match (candidate, store) {
        (Candidate::Apalis, BenchmarkStore::Sqlite(path)) => {
            run_apalis_sqlite(
                path,
                config.jobs,
                config.workers,
                config.idle_seconds,
                config.include_recovery,
            )
            .await
        }
        (Candidate::Apalis, BenchmarkStore::Postgres(url)) => {
            run_apalis_postgres(
                url,
                config.jobs,
                config.workers,
                config.idle_seconds,
                config.include_recovery,
            )
            .await
        }
        (Candidate::Azums, BenchmarkStore::Sqlite(path)) => {
            let url = format!("sqlite://{}?mode=rwc", path.display());
            run_azums(
                SqliteBackend::new(make_sqlite_pool(&url).await?),
                Database::Sqlite,
                config.jobs,
                config.workers,
                config.idle_seconds,
                config.include_recovery,
            )
            .await
        }
        (Candidate::Azums, BenchmarkStore::Postgres(url)) => {
            run_azums(
                PostgresBackend::new(make_pool(url).await?),
                Database::Postgres,
                config.jobs,
                config.workers,
                config.idle_seconds,
                config.include_recovery,
            )
            .await
        }
    }
}

struct ApalisSteadyResult {
    enqueue_samples: Vec<Duration>,
    start_samples: Vec<Duration>,
    acknowledgment_samples: Vec<Duration>,
    terminal_samples: Vec<Duration>,
    processing_seconds: f64,
    measured_idle: f64,
    idle_cpu_seconds: f64,
    completed_count: usize,
    unique_count: usize,
    handled_count: usize,
}

async fn run_apalis_steady<S, StartWorkers, TerminalIds, TerminalFuture>(
    mut control: S,
    jobs: usize,
    idle_seconds: u64,
    start_workers: StartWorkers,
    completed_ids: TerminalIds,
) -> anyhow::Result<ApalisSteadyResult>
where
    S: Storage<Job = ApalisBenchmarkJob, Context = SqlContext, Error = sqlx::Error> + Send,
    StartWorkers: FnOnce(
        Arc<ApalisBenchmarkState>,
    ) -> (Vec<Worker<Context>>, Vec<tokio::task::JoinHandle<()>>),
    TerminalIds: Fn() -> TerminalFuture,
    TerminalFuture: std::future::Future<Output = anyhow::Result<Vec<String>>>,
{
    let state = Arc::new(ApalisBenchmarkState::default());
    let mut enqueue_samples = Vec::with_capacity(jobs);
    let mut accepted_tasks = HashMap::with_capacity(jobs);
    let processing_started = Instant::now();
    for sequence in 0..jobs {
        let accepted = Instant::now();
        let parts = control.push(ApalisBenchmarkJob { sequence }).await?;
        enqueue_samples.push(accepted.elapsed());
        state.accepted.lock().await.insert(sequence, accepted);
        accepted_tasks.insert(parts.task_id.to_string(), (sequence, accepted));
    }

    let (worker_handles, worker_tasks) = start_workers(Arc::clone(&state));
    tokio::time::timeout(Duration::from_secs(120), async {
        while state.handled.load(Ordering::Acquire) < jobs {
            state.handled_notify.notified().await;
        }
    })
    .await?;

    let mut terminal_ids = HashSet::with_capacity(jobs);
    let mut acknowledgment_samples = Vec::with_capacity(jobs);
    let mut terminal_samples = Vec::with_capacity(jobs);
    tokio::time::timeout(Duration::from_secs(30), async {
        while terminal_ids.len() < jobs {
            for id in completed_ids().await? {
                if terminal_ids.insert(id.clone()) {
                    let (sequence, accepted) = accepted_tasks
                        .get(&id)
                        .ok_or_else(|| anyhow::anyhow!("completed an unknown job"))?;
                    terminal_samples.push(accepted.elapsed());
                    acknowledgment_samples.push(
                        state
                            .handled_at
                            .lock()
                            .await
                            .get(sequence)
                            .ok_or_else(|| anyhow::anyhow!("terminal job was not handled"))?
                            .elapsed(),
                    );
                }
            }
            if terminal_ids.len() < jobs {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        }
        anyhow::Ok(())
    })
    .await??;
    let processing_seconds = processing_started.elapsed().as_secs_f64();

    let idle_started = Instant::now();
    let idle_cpu_started = ProcessTime::try_now()?;
    while idle_started.elapsed() < Duration::from_secs(idle_seconds) {
        tokio::time::sleep(Duration::from_millis(250)).await;
    }
    let measured_idle = idle_started.elapsed().as_secs_f64();
    let idle_cpu_seconds = idle_cpu_started.try_elapsed()?.as_secs_f64();
    for handle in &worker_handles {
        handle.stop();
    }
    for task in worker_tasks {
        let _ = tokio::time::timeout(Duration::from_secs(2), task).await;
    }

    let unique_count = state.seen.lock().await.len();
    let start_samples = state.starts.lock().await.clone();
    Ok(ApalisSteadyResult {
        enqueue_samples,
        start_samples,
        acknowledgment_samples,
        terminal_samples,
        processing_seconds,
        measured_idle,
        idle_cpu_seconds,
        completed_count: terminal_ids.len(),
        unique_count,
        handled_count: state.handled.load(Ordering::Acquire),
    })
}

async fn run_apalis_postgres(
    url: &str,
    jobs: usize,
    workers: usize,
    idle_seconds: u64,
    include_recovery: bool,
) -> anyhow::Result<CandidateBenchmark> {
    validate_bounds(jobs, workers, idle_seconds)?;
    let pool = PgPoolOptions::new()
        .max_connections((workers + 4) as u32)
        .connect(url)
        .await?;
    PostgresStorage::<()>::setup(&pool).await?;
    let namespace = format!("printstash-qualification-steady-{}", Uuid::new_v4());
    let config = Config::new(&namespace)
        .set_buffer_size(8)
        .set_poll_interval(Duration::from_millis(20))
        .set_keep_alive(Duration::from_millis(100))
        .set_reenqueue_orphaned_after(STEADY_REENQUEUE_AFTER);
    let control = PostgresStorage::new_with_config(pool.clone(), config.clone());
    let worker_pool = pool.clone();
    let worker_config = config.clone();
    let worker_namespace = namespace.clone();
    let terminal_pool = pool.clone();
    let terminal_namespace = namespace.clone();
    let steady = run_apalis_steady(
        control,
        jobs,
        idle_seconds,
        move |state| {
            let mut worker_handles = Vec::with_capacity(workers);
            let mut worker_tasks = Vec::with_capacity(workers);
            for worker_number in 0..workers {
                let worker =
                    WorkerBuilder::new(format!("{worker_namespace}-worker-{worker_number}"))
                        .data(Arc::clone(&state))
                        .backend(PostgresStorage::new_with_config(
                            worker_pool.clone(),
                            worker_config.clone(),
                        ))
                        .build_fn(apalis_benchmark_handler);
                let running = worker.run();
                worker_handles.push(running.get_handle());
                worker_tasks.push(tokio::spawn(running));
            }
            (worker_handles, worker_tasks)
        },
        move || {
            let pool = terminal_pool.clone();
            let namespace = terminal_namespace.clone();
            async move {
                let rows: Vec<(String,)> = sqlx::query_as(
                    "SELECT id FROM apalis.jobs WHERE job_type = $1 AND status = 'Done'",
                )
                .bind(namespace)
                .fetch_all(&pool)
                .await?;
                Ok(rows.into_iter().map(|(id,)| id).collect())
            }
        },
    )
    .await?;

    let recovery_seconds = if include_recovery {
        Some(apalis_postgres_recovery(&pool).await?)
    } else {
        None
    };
    pool.close().await;
    Ok(CandidateBenchmark {
        schema_version: 1,
        measurement_protocol: "queue-candidate-v1",
        candidate: Candidate::Apalis,
        database: Database::Postgres,
        dependency_versions: dependency_versions(Candidate::Apalis),
        enabled_features: enabled_features(Candidate::Apalis),
        jobs,
        workers,
        accepted_count: jobs,
        completed_count: steady.completed_count,
        duplicate_executions: steady.handled_count.saturating_sub(steady.unique_count),
        enqueue: LatencySummary::from_durations(&steady.enqueue_samples)?,
        enqueue_to_start: LatencySummary::from_durations(&steady.start_samples)?,
        acknowledgment: LatencySummary::from_durations(&steady.acknowledgment_samples)?,
        enqueue_to_terminal: LatencySummary::from_durations(&steady.terminal_samples)?,
        processing_seconds: steady.processing_seconds,
        throughput_jobs_per_second: jobs as f64 / steady.processing_seconds,
        idle_seconds: steady.measured_idle,
        idle_cpu_seconds: steady.idle_cpu_seconds,
        recovery_seconds,
    })
}

async fn run_apalis_sqlite(
    path: &std::path::Path,
    jobs: usize,
    workers: usize,
    idle_seconds: u64,
    include_recovery: bool,
) -> anyhow::Result<CandidateBenchmark> {
    validate_bounds(jobs, workers, idle_seconds)?;
    let url = format!("sqlite://{}?mode=rwc", path.display());
    let pool = SqlitePoolOptions::new()
        .max_connections((workers + 4) as u32)
        .connect(&url)
        .await?;
    SqliteStorage::<()>::setup(&pool).await?;
    let namespace = format!("printstash-qualification-steady-{}", Uuid::new_v4());
    let config = Config::new(&namespace)
        .set_buffer_size(8)
        .set_poll_interval(Duration::from_millis(20))
        .set_keep_alive(Duration::from_millis(100))
        .set_reenqueue_orphaned_after(STEADY_REENQUEUE_AFTER);
    let control = SqliteStorage::new_with_config(pool.clone(), config.clone());
    let worker_pool = pool.clone();
    let worker_config = config.clone();
    let worker_namespace = namespace.clone();
    let terminal_pool = pool.clone();
    let terminal_namespace = namespace.clone();
    let steady = run_apalis_steady(
        control,
        jobs,
        idle_seconds,
        move |state| {
            let mut worker_handles = Vec::with_capacity(workers);
            let mut worker_tasks = Vec::with_capacity(workers);
            for worker_number in 0..workers {
                let worker =
                    WorkerBuilder::new(format!("{worker_namespace}-worker-{worker_number}"))
                        .data(Arc::clone(&state))
                        .backend(SqliteStorage::new_with_config(
                            worker_pool.clone(),
                            worker_config.clone(),
                        ))
                        .build_fn(apalis_benchmark_handler);
                let running = worker.run();
                worker_handles.push(running.get_handle());
                worker_tasks.push(tokio::spawn(running));
            }
            (worker_handles, worker_tasks)
        },
        move || {
            let pool = terminal_pool.clone();
            let namespace = terminal_namespace.clone();
            async move {
                let rows: Vec<(String,)> =
                    sqlx::query_as("SELECT id FROM Jobs WHERE job_type = ? AND status = 'Done'")
                        .bind(namespace)
                        .fetch_all(&pool)
                        .await?;
                Ok(rows.into_iter().map(|(id,)| id).collect())
            }
        },
    )
    .await?;

    let recovery_seconds = if include_recovery {
        Some(apalis_sqlite_recovery(&pool).await?)
    } else {
        None
    };
    pool.close().await;
    Ok(CandidateBenchmark {
        schema_version: 1,
        measurement_protocol: "queue-candidate-v1",
        candidate: Candidate::Apalis,
        database: Database::Sqlite,
        dependency_versions: dependency_versions(Candidate::Apalis),
        enabled_features: enabled_features(Candidate::Apalis),
        jobs,
        workers,
        accepted_count: jobs,
        completed_count: steady.completed_count,
        duplicate_executions: steady.handled_count.saturating_sub(steady.unique_count),
        enqueue: LatencySummary::from_durations(&steady.enqueue_samples)?,
        enqueue_to_start: LatencySummary::from_durations(&steady.start_samples)?,
        acknowledgment: LatencySummary::from_durations(&steady.acknowledgment_samples)?,
        enqueue_to_terminal: LatencySummary::from_durations(&steady.terminal_samples)?,
        processing_seconds: steady.processing_seconds,
        throughput_jobs_per_second: jobs as f64 / steady.processing_seconds,
        idle_seconds: steady.measured_idle,
        idle_cpu_seconds: steady.idle_cpu_seconds,
        recovery_seconds,
    })
}

async fn apalis_sqlite_recovery(pool: &SqlitePool) -> anyhow::Result<f64> {
    let namespace = format!("printstash-qualification-recovery-{}", Uuid::new_v4());
    let config = Config::new(&namespace)
        .set_buffer_size(1)
        .set_poll_interval(Duration::from_millis(20))
        .set_keep_alive(Duration::from_millis(100))
        .set_reenqueue_orphaned_after(Duration::from_secs(1));
    let mut control = SqliteStorage::new_with_config(pool.clone(), config.clone());
    let parts = control.push(ApalisBenchmarkJob { sequence: 0 }).await?;
    let old_gate = Arc::new(RecoveryGate::default());
    let old_worker = WorkerBuilder::new("terminated-worker")
        .data(Arc::clone(&old_gate))
        .backend(SqliteStorage::new_with_config(pool.clone(), config.clone()))
        .build_fn(recovery_handler);
    let old_run = old_worker.run();
    let old_task = tokio::spawn(old_run);
    tokio::time::timeout(Duration::from_secs(5), old_gate.started.notified()).await?;
    let recovery_started = Instant::now();
    old_task.abort();
    let _ = old_task.await;

    let successor_gate = Arc::new(RecoveryGate::default());
    let successor = WorkerBuilder::new("successor-worker")
        .data(Arc::clone(&successor_gate))
        .backend(SqliteStorage::new_with_config(pool.clone(), config))
        .build_fn(recovery_successor);
    let successor_run = successor.run();
    let successor_handle = successor_run.get_handle();
    let successor_task = tokio::spawn(successor_run);
    tokio::time::timeout(Duration::from_secs(5), successor_gate.started.notified()).await?;
    let recovery_seconds = recovery_started.elapsed().as_secs_f64();
    tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            let status: String = sqlx::query_scalar("SELECT status FROM Jobs WHERE id = ?")
                .bind(parts.task_id.to_string())
                .fetch_one(pool)
                .await?;
            if status == "Done" {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        anyhow::Ok(())
    })
    .await??;
    successor_handle.stop();
    let _ = tokio::time::timeout(Duration::from_secs(2), successor_task).await;
    Ok(recovery_seconds)
}

async fn apalis_postgres_recovery(pool: &sqlx::PgPool) -> anyhow::Result<f64> {
    let namespace = format!("printstash-qualification-recovery-{}", Uuid::new_v4());
    let terminated_worker = format!("terminated-worker-{}", Uuid::new_v4());
    let successor_worker = format!("successor-worker-{}", Uuid::new_v4());
    let config = Config::new(&namespace)
        .set_buffer_size(1)
        .set_poll_interval(Duration::from_millis(20))
        .set_keep_alive(Duration::from_millis(100))
        .set_reenqueue_orphaned_after(Duration::from_secs(1));
    let mut control = PostgresStorage::new_with_config(pool.clone(), config.clone());
    let parts = control.push(ApalisBenchmarkJob { sequence: 0 }).await?;
    let old_gate = Arc::new(RecoveryGate::default());
    let old_worker = WorkerBuilder::new(terminated_worker)
        .data(Arc::clone(&old_gate))
        .backend(PostgresStorage::new_with_config(
            pool.clone(),
            config.clone(),
        ))
        .build_fn(recovery_handler);
    let old_run = old_worker.run();
    let old_task = tokio::spawn(old_run);
    tokio::time::timeout(Duration::from_secs(5), old_gate.started.notified()).await?;
    let recovery_started = Instant::now();
    old_task.abort();
    let _ = old_task.await;

    let successor_gate = Arc::new(RecoveryGate::default());
    let successor = WorkerBuilder::new(successor_worker)
        .data(Arc::clone(&successor_gate))
        .backend(PostgresStorage::new_with_config(pool.clone(), config))
        .build_fn(recovery_successor);
    let successor_run = successor.run();
    let successor_handle = successor_run.get_handle();
    let successor_task = tokio::spawn(successor_run);
    tokio::time::timeout(Duration::from_secs(5), successor_gate.started.notified()).await?;
    let recovery_seconds = recovery_started.elapsed().as_secs_f64();
    tokio::time::timeout(Duration::from_secs(2), async {
        loop {
            let status: String = sqlx::query_scalar("SELECT status FROM apalis.jobs WHERE id = $1")
                .bind(parts.task_id.to_string())
                .fetch_one(pool)
                .await?;
            if status == "Done" {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        anyhow::Ok(())
    })
    .await??;
    successor_handle.stop();
    let _ = tokio::time::timeout(Duration::from_secs(2), successor_task).await;
    Ok(recovery_seconds)
}

async fn run_azums<B>(
    backend: B,
    database: Database,
    jobs: usize,
    workers: usize,
    idle_seconds: u64,
    include_recovery: bool,
) -> anyhow::Result<CandidateBenchmark>
where
    B: StorageBackend + 'static,
{
    ensure!(
        (1..=10_000).contains(&jobs),
        "jobs must be between 1 and 10000"
    );
    ensure!(
        (1..=64).contains(&workers),
        "workers must be between 1 and 64"
    );
    ensure!(
        (1..=60).contains(&idle_seconds),
        "idle_seconds must be between 1 and 60"
    );
    backend
        .run_migrations()
        .await
        .context("azums benchmark migrations")?;
    let backend: Arc<dyn StorageBackend> = Arc::new(backend);
    let queue = format!("printstash-qualification-steady-{}", Uuid::new_v4());
    let mut enqueue_samples = Vec::with_capacity(jobs);
    let accepted_at = Arc::new(Mutex::new(HashMap::with_capacity(jobs)));
    let processing_started = Instant::now();

    for sequence in 0..jobs {
        let started = Instant::now();
        let id = backend
            .enqueue(
                Job::new(
                    "qualification-benchmark",
                    serde_json::json!({"sequence": sequence}),
                )
                .queue(&queue)
                .into(),
            )
            .await
            .with_context(|| format!("azums benchmark enqueue sequence {sequence}"))?;
        enqueue_samples.push(started.elapsed());
        accepted_at.lock().await.insert(id, started);
    }

    let completed = Arc::new(AtomicUsize::new(0));
    let seen = Arc::new(Mutex::new(HashSet::with_capacity(jobs)));
    let starts = Arc::new(Mutex::new(Vec::with_capacity(jobs)));
    let completions = Arc::new(Mutex::new(Vec::with_capacity(jobs)));
    let acknowledgments = Arc::new(Mutex::new(Vec::with_capacity(jobs)));
    let mut tasks = JoinSet::new();
    for worker_number in 0..workers {
        let backend = Arc::clone(&backend);
        let queue = queue.clone();
        let accepted_at = Arc::clone(&accepted_at);
        let completed = Arc::clone(&completed);
        let seen = Arc::clone(&seen);
        let starts = Arc::clone(&starts);
        let completions = Arc::clone(&completions);
        let acknowledgments = Arc::clone(&acknowledgments);
        tasks.spawn(async move {
            let worker = format!("qualification-worker-{worker_number}");
            loop {
                if completed.load(Ordering::Acquire) >= jobs {
                    break;
                }
                let leased = backend
                    .lease_jobs_batch(&queue, &worker, 30, 8)
                    .await
                    .with_context(|| format!("azums benchmark lease by {worker}"))?;
                if leased.is_empty() {
                    tokio::time::sleep(Duration::from_millis(1)).await;
                    continue;
                }
                {
                    let mut seen = seen.lock().await;
                    for job in &leased {
                        seen.insert(job.id);
                    }
                }
                {
                    let accepted = accepted_at.lock().await;
                    let mut starts = starts.lock().await;
                    for job in &leased {
                        starts.push(
                            accepted
                                .get(&job.id)
                                .ok_or_else(|| anyhow::anyhow!("leased an unknown job"))?
                                .elapsed(),
                        );
                    }
                }
                let dataset_ids: Vec<String> =
                    leased.iter().map(|job| job.dataset_id.clone()).collect();
                let job_ids: Vec<Uuid> = leased.iter().map(|job| job.id).collect();
                let attempts = backend
                    .start_attempts_batch(&dataset_ids, &job_ids, &worker)
                    .await
                    .with_context(|| format!("azums benchmark start attempts by {worker}"))?;
                ensure!(attempts.len() == leased.len(), "attempt count changed");
                for (job_id, attempt_id, _) in attempts {
                    let acknowledgment_started = Instant::now();
                    backend
                        .mark_succeeded(job_id, attempt_id, &worker, 0)
                        .await
                        .with_context(|| {
                            format!("azums benchmark complete job {job_id} by {worker}")
                        })?;
                    acknowledgments
                        .lock()
                        .await
                        .push(acknowledgment_started.elapsed());
                    let terminal_latency = accepted_at
                        .lock()
                        .await
                        .get(&job_id)
                        .ok_or_else(|| anyhow::anyhow!("completed an unknown job"))?
                        .elapsed();
                    completions.lock().await.push(terminal_latency);
                    completed.fetch_add(1, Ordering::Release);
                }
            }
            anyhow::Ok(())
        });
    }
    tokio::time::timeout(Duration::from_secs(120), async {
        while let Some(result) = tasks.join_next().await {
            result??;
        }
        anyhow::Ok(())
    })
    .await??;
    let processing_seconds = processing_started.elapsed().as_secs_f64();
    let completed_count = completed.load(Ordering::Acquire);
    let unique_count = seen.lock().await.len();
    ensure!(
        completed_count == jobs,
        "workers did not complete every job"
    );
    ensure!(
        unique_count == jobs,
        "workers did not execute every unique job"
    );

    let idle_queue = format!("printstash-qualification-idle-{}", Uuid::new_v4());
    let idle_started = Instant::now();
    let idle_cpu_started = ProcessTime::try_now()?;
    while idle_started.elapsed() < Duration::from_secs(idle_seconds) {
        let leased = backend
            .lease_jobs_batch(&idle_queue, "qualification-idle", 30, 1)
            .await
            .context("azums benchmark idle lease")?;
        ensure!(leased.is_empty(), "idle queue returned work");
        tokio::time::sleep(Duration::from_millis(250)).await;
    }
    let measured_idle = idle_started.elapsed().as_secs_f64();
    let idle_cpu_seconds = idle_cpu_started.try_elapsed()?.as_secs_f64();

    let recovery_seconds = if include_recovery {
        let recovery_queue = format!("printstash-qualification-recovery-{}", Uuid::new_v4());
        let recovery_job = backend
            .enqueue(
                Job::new("qualification-recovery", serde_json::json!({}))
                    .queue(&recovery_queue)
                    .into(),
            )
            .await
            .context("azums benchmark enqueue recovery job")?;
        let old = backend
            .lease_jobs_batch(&recovery_queue, "terminated-worker", 1, 1)
            .await
            .context("azums benchmark initial recovery lease")?;
        ensure!(old.len() == 1 && old[0].id == recovery_job);
        let old_attempt = backend
            .start_attempts_batch(
                &[old[0].dataset_id.clone()],
                &[recovery_job],
                "terminated-worker",
            )
            .await
            .context("azums benchmark start old recovery attempt")?;
        let recovery_started = Instant::now();
        tokio::time::sleep(Duration::from_millis(1100)).await;
        ensure!(
            backend
                .reap_expired_locks()
                .await
                .context("azums benchmark reap expired recovery lease")?
                >= 1
        );
        let successor = backend
            .lease_jobs_batch(&recovery_queue, "successor-worker", 30, 1)
            .await
            .context("azums benchmark successor recovery lease")?;
        ensure!(successor.len() == 1 && successor[0].id == recovery_job);
        let successor_attempt = backend
            .start_attempts_batch(
                &[successor[0].dataset_id.clone()],
                &[recovery_job],
                "successor-worker",
            )
            .await
            .context("azums benchmark start successor recovery attempt")?;
        let recovery_seconds = recovery_started.elapsed().as_secs_f64();
        ensure!(
            backend
                .mark_succeeded(recovery_job, old_attempt[0].1, "terminated-worker", 0)
                .await
                .is_err(),
            "terminated worker completion was accepted after recovery"
        );
        backend
            .mark_succeeded(recovery_job, successor_attempt[0].1, "successor-worker", 0)
            .await
            .context("azums benchmark complete successor recovery attempt")?;
        Some(recovery_seconds)
    } else {
        None
    };

    let start_samples = starts.lock().await.clone();
    let acknowledgment_samples = acknowledgments.lock().await.clone();
    let completion_samples = completions.lock().await.clone();
    Ok(CandidateBenchmark {
        schema_version: 1,
        measurement_protocol: "queue-candidate-v1",
        candidate: Candidate::Azums,
        database,
        dependency_versions: dependency_versions(Candidate::Azums),
        enabled_features: enabled_features(Candidate::Azums),
        jobs,
        workers,
        accepted_count: jobs,
        completed_count,
        duplicate_executions: completed_count.saturating_sub(unique_count),
        enqueue: LatencySummary::from_durations(&enqueue_samples)?,
        enqueue_to_start: LatencySummary::from_durations(&start_samples)?,
        acknowledgment: LatencySummary::from_durations(&acknowledgment_samples)?,
        enqueue_to_terminal: LatencySummary::from_durations(&completion_samples)?,
        processing_seconds,
        throughput_jobs_per_second: jobs as f64 / processing_seconds,
        idle_seconds: measured_idle,
        idle_cpu_seconds,
        recovery_seconds,
    })
}

fn percentile(samples: &[f64], percentile: f64) -> f64 {
    let index = ((samples.len() - 1) as f64 * percentile).ceil() as usize;
    samples[index.min(samples.len() - 1)]
}

fn validate_bounds(jobs: usize, workers: usize, idle_seconds: u64) -> anyhow::Result<()> {
    ensure!(
        (1..=10_000).contains(&jobs),
        "jobs must be between 1 and 10000"
    );
    ensure!(
        (1..=64).contains(&workers),
        "workers must be between 1 and 64"
    );
    ensure!(
        (1..=60).contains(&idle_seconds),
        "idle_seconds must be between 1 and 60"
    );
    Ok(())
}
