use std::collections::BTreeMap;

use serde::Serialize;
use serde_json::Value;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Candidate {
    Apalis,
    Azums,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Database {
    Sqlite,
    Postgres,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Contract {
    AtomicAcceptanceCommit,
    AtomicAcceptanceRollback,
    RejectCompletionAfterExpiry,
    RejectRenewalAfterExpiry,
    RejectStaleCompletion,
    RejectReusedWorkerStaleCompletion,
    RetryFailedWork,
    RecoverInterruptedWork,
    OperateUnderContention,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Observation {
    pub schema_version: u8,
    pub candidate: Candidate,
    pub database: Database,
    pub contract: Contract,
    pub meets_contract: bool,
    pub expected: &'static str,
    pub observed: Value,
}

#[derive(Clone, Debug, Serialize)]
pub struct QualificationReport {
    pub schema_version: u8,
    pub measurement_protocol: &'static str,
    pub candidate: Candidate,
    pub database: Database,
    pub dependency_versions: BTreeMap<&'static str, &'static str>,
    pub enabled_features: Vec<&'static str>,
    pub observations: Vec<Observation>,
}

impl QualificationReport {
    pub fn new(candidate: Candidate, database: Database, observations: Vec<Observation>) -> Self {
        Self {
            schema_version: 1,
            measurement_protocol: "queue-qualification-contracts-v1",
            candidate,
            database,
            dependency_versions: dependency_versions(candidate),
            enabled_features: enabled_features(candidate),
            observations,
        }
    }
}

pub fn dependency_versions(candidate: Candidate) -> BTreeMap<&'static str, &'static str> {
    let mut versions = BTreeMap::from([("sqlx", "0.8.6"), ("tokio", "1.53.1"), ("uuid", "1.26.1")]);
    match candidate {
        Candidate::Apalis => {
            versions.insert("apalis", "0.7.4");
            versions.insert("apalis-sql", "0.7.4");
        }
        Candidate::Azums => {
            versions.insert("azums", "1.0.1");
        }
    }
    versions
}

pub fn enabled_features(candidate: Candidate) -> Vec<&'static str> {
    match candidate {
        Candidate::Apalis => vec![
            "apalis/default-features=false",
            "apalis-sql/migrate",
            "apalis-sql/postgres",
            "apalis-sql/sqlite",
            "apalis-sql/tokio-comp",
        ],
        Candidate::Azums => vec![
            "azums/default-features=false",
            "azums/postgres",
            "azums/sqlite",
        ],
    }
}

impl Observation {
    pub(crate) fn new(
        candidate: Candidate,
        database: Database,
        contract: Contract,
        meets_contract: bool,
        expected: &'static str,
        observed: Value,
    ) -> Self {
        Self {
            schema_version: 1,
            candidate,
            database,
            contract,
            meets_contract,
            expected,
            observed,
        }
    }
}
