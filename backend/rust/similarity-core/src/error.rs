use std::fmt;

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    AlignmentFailed,
    EmptyOccupancy,
    InvalidAlignment,
    InvalidEquivalenceTransform,
    InvalidFaces,
    InvalidInertiaGeometry,
    InvalidNeighborPoints,
    InvalidProximityBudget,
    InvalidProximityPoints,
    InvalidProximitySurface,
    InvalidVerificationMetrics,
    InvalidVertices,
    InvalidViewImage,
    InvalidVoxelRecipe,
    NonfiniteGeometry,
    NumericRange,
    ProximityWorkLimit,
    VoxelResourceLimit,
}

impl Error {
    pub const fn code(self) -> &'static str {
        match self {
            Self::AlignmentFailed => "alignment_failed",
            Self::EmptyOccupancy => "empty_occupancy",
            Self::InvalidAlignment => "invalid_alignment",
            Self::InvalidEquivalenceTransform => "invalid_equivalence_transform",
            Self::InvalidFaces => "invalid_faces",
            Self::InvalidInertiaGeometry => "invalid_inertia_geometry",
            Self::InvalidNeighborPoints => "invalid_neighbor_points",
            Self::InvalidProximityBudget => "invalid_proximity_budget",
            Self::InvalidProximityPoints => "invalid_proximity_points",
            Self::InvalidProximitySurface => "invalid_proximity_surface",
            Self::InvalidVerificationMetrics => "invalid_verification_metrics",
            Self::InvalidVertices => "invalid_vertices",
            Self::InvalidViewImage => "invalid_view_image",
            Self::InvalidVoxelRecipe => "invalid_voxel_recipe",
            Self::NonfiniteGeometry => "nonfinite_geometry",
            Self::NumericRange => "numeric_range",
            Self::ProximityWorkLimit => "proximity_work_limit",
            Self::VoxelResourceLimit => "voxel_resource_limit",
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code())
    }
}

impl std::error::Error for Error {}
