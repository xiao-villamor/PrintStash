#![cfg(feature = "postgres-tests")]

use printstash_queue_qualification::{
    adapters::{
        apalis_postgres_atomic_acceptance, azums_postgres_atomic_acceptance,
        postgres_expired_ownership, postgres_process_recovery, postgres_stale_completion,
        postgres_successor_fencing,
    },
    Candidate, Contract, Database,
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

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn released_apalis_owner_overwrites_its_successor() {
    let url = database_url("apalis");
    let atomic = apalis_postgres_atomic_acceptance(&url)
        .await
        .expect("atomic qualification scenario should execute");
    assert_eq!(atomic.contract, Contract::AtomicAcceptanceRollback);
    assert!(!atomic.meets_contract, "{atomic:#?}");

    let observation = postgres_stale_completion(&url)
        .await
        .expect("qualification scenario should execute");

    assert_eq!(observation.candidate, Candidate::Apalis);
    assert_eq!(observation.database, Database::Postgres);
    assert_eq!(observation.contract, Contract::RejectStaleCompletion);
    assert!(!observation.meets_contract, "{observation:#?}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn apalis_recovers_after_worker_process_termination() {
    let observation = postgres_process_recovery(
        &database_url("apalis"),
        std::path::Path::new(env!("CARGO_BIN_EXE_queue-qualification")),
    )
    .await
    .expect("process-loss qualification scenario should execute");

    assert_eq!(observation.candidate, Candidate::Apalis);
    assert_eq!(observation.database, Database::Postgres);
    assert_eq!(observation.contract, Contract::RecoverInterruptedWork);
    assert!(observation.meets_contract, "{observation:#?}");
}

#[tokio::test]
async fn azums_has_atomic_acceptance_but_accepts_expired_owners() {
    let url = database_url("azums");
    let atomic = azums_postgres_atomic_acceptance(&url)
        .await
        .expect("atomic qualification scenarios should execute");
    assert_eq!(atomic.len(), 2);
    for observation in atomic {
        assert_eq!(observation.candidate, Candidate::Azums);
        assert_eq!(observation.database, Database::Postgres);
        assert!(observation.meets_contract, "{observation:#?}");
    }

    let fencing = postgres_successor_fencing(&url)
        .await
        .expect("successor fencing scenarios should execute");
    assert_eq!(fencing.len(), 2);
    for observation in fencing {
        assert_eq!(observation.candidate, Candidate::Azums);
        assert_eq!(observation.database, Database::Postgres);
        assert!(observation.meets_contract, "{observation:#?}");
    }

    let observations = postgres_expired_ownership(&url)
        .await
        .expect("qualification scenario should execute");

    assert_eq!(observations.len(), 2);
    for observation in observations {
        assert_eq!(observation.candidate, Candidate::Azums);
        assert_eq!(observation.database, Database::Postgres);
        assert!(!observation.meets_contract, "{observation:#?}");
    }
}
