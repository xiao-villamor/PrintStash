"""Closed source/expectation contracts for the v2 benchmark corpus; no generator imports."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

MAX_EXTERNAL_BYTES = 64 * 1024 * 1024


class FileType(StrEnum):
    STL = "stl"
    THREE_MF = "3mf"


class Profile(StrEnum):
    SMALL = "small"
    FULL = "full"
    DOWNLOAD = "download"


class Family(StrEnum):
    BINARY = "stl_binary"
    ASCII = "stl_ascii"
    CORE = "3mf_core"
    PRODUCTION = "3mf_production"
    SLICER = "slicer_projects"
    GEOMETRY = "geometry"
    PRECISION = "precision"
    LOAD = "load"


class VolumeContract(StrEnum):
    CERTIFIED_MAGNITUDE = "certified_magnitude"
    UNKNOWN = "unknown"


class Policy(StrEnum):
    TRUNCATED = "truncated_facet"
    COUNT = "count_mismatch"
    TRAILING = "trailing_bytes_rejected"
    NONFINITE = "nonfinite_coordinates"
    INCOMPLETE = "incomplete_ascii_facet"
    EMPTY = "empty_geometry"
    INDEX = "invalid_index"
    DUPLICATE_ID = "duplicate_resource_id"
    CYCLE = "component_cycle"
    SINGULAR = "singular_transform"
    EXPANSION = "expanded_scene_limit"
    REQUIRED = "unsupported_required_extension"
    SLICER = "compatible_slicer_project"


@dataclass(frozen=True)
class ExpectedGeometry:
    triangle_count: int
    bbox_mm: tuple[float, float, float]
    volume_mm3: float | None
    volume_contract: VolumeContract
    relative_tolerance: float = 1e-6
    absolute_tolerance: float = 0.0
    outcome: Literal["accept"] = field(default="accept", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.volume_contract, VolumeContract):
            raise ValueError("invalid volume contract")
        if type(self.triangle_count) is not int or self.triangle_count < 0:
            raise ValueError("invalid triangle expectation")
        if len(self.bbox_mm) != 3 or any(
            type(v) not in (int, float) or not math.isfinite(v) or v < 0
            for v in self.bbox_mm
        ):
            raise ValueError("invalid bounds expectation")
        if (self.volume_mm3 is None) != (
            self.volume_contract is VolumeContract.UNKNOWN
        ):
            raise ValueError("volume contract disagrees with expectation")
        if self.volume_mm3 is not None and (
            type(self.volume_mm3) not in (int, float)
            or not math.isfinite(self.volume_mm3)
            or self.volume_mm3 < 0
        ):
            raise ValueError("invalid volume expectation")


@dataclass(frozen=True)
class ExpectedRefusal:
    rule: Policy
    outcome: Literal["refuse"] = field(default="refuse", init=False)


@dataclass(frozen=True)
class ExpectedCompatibility:
    rule: Policy = Policy.SLICER
    outcome: Literal["accept"] = field(default="accept", init=False)


Expectation = ExpectedGeometry | ExpectedRefusal | ExpectedCompatibility


@dataclass(frozen=True)
class CorpusFixture:
    filename: str
    file_type: FileType
    sha256: str
    input_bytes: int
    source_faces: int | None
    resources: int | None
    instances: int | None
    expectation: Expectation
    family: Family
    profile: Profile = Profile.SMALL
    origin: str = "generated:scripts.mesh_benchmark_corpus/v2"
    license: str = "AGPL-3.0"


@dataclass(frozen=True)
class ExternalReference:
    filename: str
    case: str
    repository: str
    revision: str
    path: str
    sha256: str
    input_bytes: int
    url: str
    licensing: str

    def __post_init__(self) -> None:
        if Path(self.filename).name != self.filename or not self.filename.endswith(
            ".3mf"
        ):
            raise ValueError("external filename must be a flat 3MF name")
        if (
            len(self.sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.sha256)
            or self.input_bytes < 1
            or self.input_bytes > MAX_EXTERNAL_BYTES
        ):
            raise ValueError("invalid pinned external identity")
        prefix = f"https://raw.githubusercontent.com/{self.repository}/{self.revision}/"
        if len(self.revision) != 40 or self.url != prefix + self.path:
            raise ValueError("external URL must use its pinned revision")


class RecoveryKind(StrEnum):
    CACHE_CORRUPTION = "cache_corruption"
    DISK_FULL = "disk_full"
    CHILD_KILLED = "child_sigkill"
    PARENT_KILLED = "parent_sigkill"
    TIMEOUT = "worker_timeout"
    CANCEL_WAITING = "cancel_waiting"
    CANCEL_RUNNING = "cancel_running"
    CANCEL_PUBLISHING = "cancel_publishing"
    RETRY = "retry_after_failure"
    REGENERATE = "concurrent_regeneration"


@dataclass(frozen=True)
class RecoveryScenario:
    kind: RecoveryKind
    precondition: str
    expected_contract: str
    execution: Literal["harness_scenario_not_a_fixture"] = field(
        default="harness_scenario_not_a_fixture", init=False
    )


@dataclass(frozen=True)
class CorpusManifest:
    fixtures: tuple[CorpusFixture, ...]
    external_references: tuple[ExternalReference, ...]
    recovery_scenarios: tuple[RecoveryScenario, ...]
    schema_version: int = 2
    corpus_id: str = "mesh-contract-v2"
    expectation_scope: str = "target_contract_not_observed_compliance"
