"""Data-only closed causes and geometry representation facts for mesh owners."""

from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import TypeAlias


class FingerprintFailureCode(StrEnum):
    """Existing geometry, sampling and supervision wire codes; no arbitrary identifiers."""

    ALGORITHM_BASIS_MISMATCH = "algorithm_basis_mismatch"
    ANALYSIS_FAILED = "analysis_failed"
    ANALYSIS_UNAVAILABLE = "analysis_unavailable"
    ARCHIVE_RESOURCE_LIMIT = "archive_resource_limit"
    COMPONENT_RESOURCE_LIMIT = "component_resource_limit"
    COMPONENT_UNAVAILABLE = "component_unavailable"
    CYCLIC_RESOURCE = "cyclic_resource"
    DEGENERATE_HULL = "degenerate_hull"
    DEGENERATE_SURFACE = "degenerate_surface"
    DEGENERATE_TRANSFORM = "degenerate_transform"
    DUPLICATE_ARCHIVE_ENTRY = "duplicate_archive_entry"
    DUPLICATE_RESOURCE = "duplicate_resource"
    EMBEDDING_REQUIRES_COMPLETE_GEOMETRY = "embedding_requires_complete_geometry"
    EMBEDDING_VIEW_FAILED = "embedding_view_failed"
    EMPTY_OCCUPANCY = "empty_occupancy"
    EMPTY_SCENE = "empty_scene"
    EMPTY_TARGET = "empty_target"
    GEOMETRY_WORK_LIMIT = "geometry_work_limit"
    HULL_RESOURCE_LIMIT = "hull_resource_limit"
    INVALID_3MF = "invalid_3mf"
    INVALID_3MF_MODEL = "invalid_3mf_model"
    INVALID_3MF_RELATIONSHIP = "invalid_3mf_relationship"
    INVALID_ARCHIVE = "invalid_archive"
    INVALID_BASIS_DIGEST = "invalid_basis_digest"
    INVALID_BASIS_SHAPE = "invalid_basis_shape"
    INVALID_BUDGET = "invalid_budget"
    INVALID_COMPONENT_BUDGET = "invalid_component_budget"
    INVALID_FACES = "invalid_faces"
    INVALID_GEOMETRY = "invalid_geometry"
    INVALID_HULL_BUDGET = "invalid_hull_budget"
    INVALID_POINT_CLOUD = "invalid_point_cloud"
    INVALID_PROXIMITY_BUDGET = "invalid_proximity_budget"
    INVALID_PROXIMITY_POINTS = "invalid_proximity_points"
    INVALID_REQUIRED_EXTENSION = "invalid_required_extension"
    INVALID_RESOURCE_ID = "invalid_resource_id"
    INVALID_RESOURCE_PATH = "invalid_resource_path"
    INVALID_SAMPLE_COUNT = "invalid_sample_count"
    INVALID_SCENE_BUDGET = "invalid_scene_budget"
    INVALID_SOURCE = "invalid_source"
    INVALID_STEP = "invalid_step"
    INVALID_TRANSFORM = "invalid_transform"
    INVALID_TRIANGLE_CAP = "invalid_triangle_cap"
    INVALID_VERIFICATION_BUDGET = "invalid_verification_budget"
    INVALID_VERTICES = "invalid_vertices"
    INVALID_VIEW_BUDGET = "invalid_view_budget"
    INVALID_VIEW_IMAGE = "invalid_view_image"
    INVALID_VOXEL_RECIPE = "invalid_voxel_recipe"
    MISSING_RESOURCE = "missing_resource"
    NO_GEOMETRY = "no_geometry"
    NONFINITE_GEOMETRY = "nonfinite_geometry"
    NUMERIC_RANGE = "numeric_range"
    POINT_LIMIT = "point_limit"
    PROXIMITY_WORK_LIMIT = "proximity_work_limit"
    RENDERER_NO_OUTPUT = "renderer_no_output"
    RESOURCE_LIMIT = "resource_limit"
    SAMPLED_OVERSIZED_SOURCE = "sampled_oversized_source"
    SAMPLED_SOURCE = "sampled_source"
    SCENE_DEPTH_LIMIT = "scene_depth_limit"
    SCENE_RESOURCE_LIMIT = "scene_resource_limit"
    SOURCE_CHANGED = "source_changed"
    SOURCE_UNAVAILABLE = "source_unavailable"
    STEP_UNAVAILABLE = "step_unavailable"
    STORAGE = "storage"
    TESSELLATION_TIMEOUT = "tessellation_timeout"
    TIMEOUT = "timeout"
    UNCALIBRATED_BASIS = "uncalibrated_basis"
    UNSAFE_RESOURCE_PATH = "unsafe_resource_path"
    UNSUPPORTED_3MF_CAPABILITY = "unsupported_3mf_capability"
    UNSUPPORTED_FORMAT = "unsupported_format"
    UNSUPPORTED_GEOMETRY = "unsupported_geometry"
    UNSUPPORTED_UNIT = "unsupported_unit"
    VERIFICATION_TIME_LIMIT = "verification_time_limit"
    VIEW_RENDER_UNAVAILABLE = "view_render_unavailable"
    VOXEL_RESOURCE_LIMIT = "voxel_resource_limit"
    WORKER_FAILED = "worker_failed"
    WORKER_OOM = "worker_oom"
    XML_DOCTYPE_FORBIDDEN = "xml_doctype_forbidden"


class FingerprintResultState(Enum):
    READY = "ready"
    PARTIAL = "partial"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class CompleteGeometry:
    """The prepared representation contains every source face."""


@dataclass(frozen=True)
class SampledGeometry:
    """A bounded representation whose source-wide topology is unassessed."""

    reason: FingerprintFailureCode

    def __post_init__(self) -> None:
        if not isinstance(self.reason, FingerprintFailureCode):
            raise TypeError("invalid_sampled_geometry_reason")
        if self.reason not in {
            FingerprintFailureCode.SAMPLED_SOURCE,
            FingerprintFailureCode.SAMPLED_OVERSIZED_SOURCE,
        }:
            raise ValueError("invalid_sampled_geometry_reason")


PreparedGeometry: TypeAlias = CompleteGeometry | SampledGeometry
