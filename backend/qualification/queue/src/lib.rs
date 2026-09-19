//! Executable qualification evidence for durable queue candidates.
//!
//! This crate is test infrastructure. Candidate types remain inside adapters and
//! never cross into PrintStash production modules.

pub mod adapters;
pub mod benchmark;
mod evidence;

pub use evidence::{Candidate, Contract, Database, Observation, QualificationReport};
