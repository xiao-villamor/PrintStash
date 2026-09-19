use std::{
    path::{Path, PathBuf},
    sync::atomic::{AtomicU64, Ordering},
};

use printstash_queue_qualification::{
    adapters::{
        apalis_sqlite_atomic_acceptance, azums_sqlite_atomic_acceptance,
        sqlite_distinct_worker_completion, sqlite_expired_ownership, sqlite_failed_recovery,
        sqlite_reused_worker_completion, sqlite_successor_fencing,
    },
    Candidate, Contract, Database,
};

static NEXT_DATABASE: AtomicU64 = AtomicU64::new(0);

struct TestDatabase(PathBuf);

impl TestDatabase {
    fn new(label: &str) -> Self {
        let sequence = NEXT_DATABASE.fetch_add(1, Ordering::Relaxed);
        Self(std::env::temp_dir().join(format!(
            "printstash-queue-qualification-{label}-{}-{sequence}.sqlite",
            std::process::id()
        )))
    }

    fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for TestDatabase {
    fn drop(&mut self) {
        for suffix in ["", "-shm", "-wal"] {
            let _ = std::fs::remove_file(format!("{}{suffix}", self.0.display()));
        }
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn apalis_does_not_fence_a_reused_worker_attempt() {
    let database = TestDatabase::new("apalis-reused-worker");
    let observation = sqlite_reused_worker_completion(database.path())
        .await
        .expect("qualification scenario should execute");

    assert_eq!(observation.candidate, Candidate::Apalis);
    assert_eq!(observation.database, Database::Sqlite);
    assert_eq!(
        observation.contract,
        Contract::RejectReusedWorkerStaleCompletion
    );
    assert!(!observation.meets_contract, "{observation:#?}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn apalis_sqlite_fences_a_distinct_successor_worker() {
    let database = TestDatabase::new("apalis-distinct-worker");
    let observation = sqlite_distinct_worker_completion(database.path())
        .await
        .expect("qualification scenario should execute");

    assert_eq!(observation.candidate, Candidate::Apalis);
    assert_eq!(observation.database, Database::Sqlite);
    assert_eq!(observation.contract, Contract::RejectStaleCompletion);
    assert!(observation.meets_contract, "{observation:#?}");
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn apalis_strands_a_retryable_failure_for_a_fresh_worker() {
    let database = TestDatabase::new("apalis-failed-recovery");
    let observation = sqlite_failed_recovery(database.path())
        .await
        .expect("qualification scenario should execute");

    assert_eq!(observation.candidate, Candidate::Apalis);
    assert_eq!(observation.database, Database::Sqlite);
    assert_eq!(observation.contract, Contract::RetryFailedWork);
    assert!(!observation.meets_contract, "{observation:#?}");
}

#[tokio::test]
async fn apalis_enqueue_cannot_join_the_application_transaction() {
    let database = TestDatabase::new("apalis-atomicity");
    let observation = apalis_sqlite_atomic_acceptance(database.path())
        .await
        .expect("qualification scenario should execute");

    assert_eq!(observation.candidate, Candidate::Apalis);
    assert_eq!(observation.database, Database::Sqlite);
    assert_eq!(observation.contract, Contract::AtomicAcceptanceRollback);
    assert!(!observation.meets_contract, "{observation:#?}");
}

#[tokio::test]
async fn azums_has_atomic_acceptance_but_accepts_expired_owners() {
    let database = TestDatabase::new("azums-expiry");
    let atomic = azums_sqlite_atomic_acceptance(database.path())
        .await
        .expect("atomic qualification scenarios should execute");
    assert_eq!(atomic.len(), 2);
    for observation in atomic {
        assert_eq!(observation.candidate, Candidate::Azums);
        assert_eq!(observation.database, Database::Sqlite);
        assert!(observation.meets_contract, "{observation:#?}");
    }

    let fencing = sqlite_successor_fencing(database.path())
        .await
        .expect("successor fencing scenarios should execute");
    assert_eq!(fencing.len(), 2);
    for observation in fencing {
        assert_eq!(observation.candidate, Candidate::Azums);
        assert_eq!(observation.database, Database::Sqlite);
        assert!(observation.meets_contract, "{observation:#?}");
    }

    let observations = sqlite_expired_ownership(database.path())
        .await
        .expect("qualification scenario should execute");

    assert_eq!(observations.len(), 2);
    for observation in observations {
        assert_eq!(observation.candidate, Candidate::Azums);
        assert_eq!(observation.database, Database::Sqlite);
        assert!(!observation.meets_contract, "{observation:#?}");
    }
}
