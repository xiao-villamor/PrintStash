use std::path::PathBuf;

use printstash_queue_qualification::adapters::{
    apalis_postgres_atomic_acceptance, apalis_sqlite_atomic_acceptance,
    azums_postgres_atomic_acceptance, azums_sqlite_atomic_acceptance, azums_sqlite_contention,
    postgres_expired_ownership, postgres_process_recovery, postgres_stale_completion,
    postgres_successor_fencing, run_postgres_blocking_worker, sqlite_distinct_worker_completion,
    sqlite_expired_ownership, sqlite_failed_recovery, sqlite_reused_worker_completion,
    sqlite_successor_fencing,
};
use printstash_queue_qualification::benchmark::{run_candidate, BenchmarkConfig, BenchmarkStore};
use printstash_queue_qualification::{Candidate, Database, QualificationReport};

fn required(name: &str) -> anyhow::Result<String> {
    std::env::var(name).map_err(|_| anyhow::anyhow!("{name} is required"))
}

fn bounded_number(
    name: &str,
    default: usize,
    range: std::ops::RangeInclusive<usize>,
) -> anyhow::Result<usize> {
    let value = match std::env::var(name) {
        Ok(value) => value.parse()?,
        Err(std::env::VarError::NotPresent) => default,
        Err(error) => return Err(error.into()),
    };
    anyhow::ensure!(
        range.contains(&value),
        "{name} is outside its supported bounds"
    );
    Ok(value)
}

fn boolean(name: &str, default: bool) -> anyhow::Result<bool> {
    match std::env::var(name) {
        Ok(value) if value == "true" => Ok(true),
        Ok(value) if value == "false" => Ok(false),
        Ok(_) => anyhow::bail!("{name} must be true or false"),
        Err(std::env::VarError::NotPresent) => Ok(default),
        Err(error) => Err(error.into()),
    }
}

async fn benchmark(candidate: &str, database: &str, root: &std::path::Path) -> anyhow::Result<()> {
    let jobs = bounded_number("PRINTSTASH_QUEUE_QUALIFICATION_JOBS", 128, 1..=10_000)?;
    let workers = bounded_number("PRINTSTASH_QUEUE_QUALIFICATION_WORKERS", 1, 1..=64)?;
    let idle_seconds =
        bounded_number("PRINTSTASH_QUEUE_QUALIFICATION_IDLE_SECONDS", 10, 1..=60)? as u64;
    let include_recovery = boolean("PRINTSTASH_QUEUE_QUALIFICATION_RECOVERY", true)?;
    let selected_candidate = match candidate {
        "apalis" => Candidate::Apalis,
        "azums" => Candidate::Azums,
        _ => anyhow::bail!("unsupported qualification candidate: {candidate}"),
    };
    let postgres_url = match database {
        "sqlite" => None,
        "postgres" => Some(required("PRINTSTASH_QUEUE_QUALIFICATION_POSTGRES_URL")?),
        _ => anyhow::bail!("unsupported qualification database: {database}"),
    };
    let sqlite_path = root.join(format!("{candidate}-benchmark.sqlite"));
    let store = match postgres_url.as_deref() {
        Some(url) => BenchmarkStore::Postgres(url),
        None => BenchmarkStore::Sqlite(&sqlite_path),
    };
    let report = run_candidate(
        selected_candidate,
        store,
        BenchmarkConfig {
            jobs,
            workers,
            idle_seconds,
            include_recovery,
        },
    )
    .await?;
    let output = serde_json::to_string_pretty(&report)?;
    std::fs::write(
        root.join(format!("{candidate}-{database}-benchmark.json")),
        format!("{output}\n"),
    )?;
    println!("{output}");
    Ok(())
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    if std::env::var("PRINTSTASH_QUEUE_QUALIFICATION_MODE").as_deref()
        == Ok("apalis-postgres-blocking-worker")
    {
        return run_postgres_blocking_worker(
            &required("PRINTSTASH_QUEUE_QUALIFICATION_POSTGRES_URL")?,
            &required("PRINTSTASH_QUEUE_QUALIFICATION_NAMESPACE")?,
            &required("PRINTSTASH_QUEUE_QUALIFICATION_WORKER_ID")?,
        )
        .await;
    }
    let candidate = required("PRINTSTASH_QUEUE_QUALIFICATION_CANDIDATE")?;
    let database = required("PRINTSTASH_QUEUE_QUALIFICATION_DATABASE")?;
    let root = PathBuf::from(required("PRINTSTASH_QUEUE_QUALIFICATION_ROOT")?);
    std::fs::create_dir_all(&root)?;
    if std::env::var("PRINTSTASH_QUEUE_QUALIFICATION_MODE").as_deref() == Ok("benchmark") {
        return benchmark(&candidate, &database, &root).await;
    }

    let observations = match (candidate.as_str(), database.as_str()) {
        ("apalis", "sqlite") => vec![
            apalis_sqlite_atomic_acceptance(&root.join("apalis-atomicity.sqlite")).await?,
            sqlite_distinct_worker_completion(&root.join("apalis-distinct-worker.sqlite")).await?,
            sqlite_reused_worker_completion(&root.join("apalis-reused-worker.sqlite")).await?,
            sqlite_failed_recovery(&root.join("apalis-failed-recovery.sqlite")).await?,
        ],
        ("apalis", "postgres") => {
            let url = required("PRINTSTASH_QUEUE_QUALIFICATION_POSTGRES_URL")?;
            vec![
                apalis_postgres_atomic_acceptance(&url).await?,
                postgres_stale_completion(&url).await?,
                postgres_process_recovery(&url, &std::env::current_exe()?).await?,
            ]
        }
        ("azums", "sqlite") => {
            let database_path = root.join("azums.sqlite");
            let mut observations = azums_sqlite_atomic_acceptance(&database_path).await?;
            observations.extend(sqlite_successor_fencing(&database_path).await?);
            observations.extend(sqlite_expired_ownership(&database_path).await?);
            observations
                .push(azums_sqlite_contention(&root.join("azums-contention.sqlite")).await?);
            observations
        }
        ("azums", "postgres") => {
            let url = required("PRINTSTASH_QUEUE_QUALIFICATION_POSTGRES_URL")?;
            let mut observations = azums_postgres_atomic_acceptance(&url).await?;
            observations.extend(postgres_successor_fencing(&url).await?);
            observations.extend(postgres_expired_ownership(&url).await?);
            observations
        }
        _ => anyhow::bail!(
            "unsupported qualification selection: candidate={candidate}, database={database}"
        ),
    };

    let selected_candidate = match candidate.as_str() {
        "apalis" => Candidate::Apalis,
        "azums" => Candidate::Azums,
        _ => unreachable!("selection was validated by the scenario match"),
    };
    let selected_database = match database.as_str() {
        "sqlite" => Database::Sqlite,
        "postgres" => Database::Postgres,
        _ => unreachable!("selection was validated by the scenario match"),
    };
    let report = QualificationReport::new(selected_candidate, selected_database, observations);
    let output = serde_json::to_string_pretty(&report)?;
    std::fs::write(
        root.join(format!("{candidate}-{database}.json")),
        format!("{output}\n"),
    )?;
    println!("{output}");
    Ok(())
}
