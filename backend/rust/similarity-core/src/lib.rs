//! Bounded geometry comparison primitives with no Python or application dependency.
#![forbid(unsafe_code)]

mod descriptors;
mod error;
mod mesh;
mod proximity;
mod verification;

pub use descriptors::{
    dct_hash, equivalent_triangles, nearest_neighbors, sh_spectrum, volume_inertia_ratios, voxelize,
};
pub use error::{Error, Result};
pub use mesh::{Face, Mesh, Point, Transform, Triangle};
pub use proximity::{Alignment, ClosestPoint, SurfaceTree};
pub use verification::{
    verification_decision, EvidenceClass, VerificationDecision, VerificationMetrics,
};
