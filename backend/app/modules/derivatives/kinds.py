"""Which derivatives exist, which Artifacts they apply to, and their recipes.

A derivative is a pure function of one Artifact's bytes plus a recipe. Kinds
are grouped by the producer that computes them together: one mesh load yields
geometry and a rendered thumbnail, one G-code header read yields slicer
metadata and the embedded thumbnail. Each group is one job definition; each
kind in it has its own recipe version.

**Bumping a recipe.** A recipe version is the code's statement that the output
of a kind would now differ for the same bytes. Increase it, by hand, in the
same change that alters what the producer emits for a kind (a new renderer, a
parser that reads a field it used to miss, a different encoding). Nothing else
is needed: the derivative source finds every Artifact without a row at the new
version with an anti-join and re-derives it at backfill priority, and each old
output stays visible until its replacement is ready. Do not bump for a refactor
that cannot change any output. See ``docs/derivatives.md``.

An Artifact belongs to exactly one group (it is a mesh or it is G-code), so a
kind's recipe version is read relative to the Artifact's group.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import and_, func, or_
from sqlmodel import col

from app.db.models import SENTINEL_FILE_HASH, DerivativeKind, File, FileType, JobKind

# Recipe versions. Bump rule: see the module docstring.
MESH_GEOMETRY_RECIPE = 2
MESH_THUMBNAIL_RECIPE = 2
GCODE_METADATA_RECIPE = 1
GCODE_THUMBNAIL_RECIPE = 1
TOOLPATH_RECIPE = 1

MESH_TYPES = (FileType.STL, FileType.THREE_MF, FileType.OBJ, FileType.STEP)
BINARY_GCODE_SUFFIXES = (".bgcode", ".bgc")


@dataclass(frozen=True)
class DerivativeGroup:
    """One producer: a job definition and the kinds it derives together.

    ``applies`` is the SQL predicate over ``files`` selecting the Artifacts
    this group derives for; ``applies_to`` is the same rule for one row.
    Both are stated per group, so a group is never assumed by elimination.
    """

    definition: JobKind
    kinds: dict[DerivativeKind, int]
    label: str
    applies: Callable[[], Any]
    applies_to: Callable[[File], bool]


def _real() -> Any:
    return col(File.sha256) != SENTINEL_FILE_HASH


def _is_binary_gcode(file: File) -> bool:
    return file.file_type == FileType.GCODE and file.original_filename.lower().endswith(
        BINARY_GCODE_SUFFIXES
    )


GROUPS: tuple[DerivativeGroup, ...] = (
    DerivativeGroup(
        definition=JobKind.DERIVATIVES_MESH,
        kinds={
            DerivativeKind.METADATA: MESH_GEOMETRY_RECIPE,
            DerivativeKind.THUMBNAIL: MESH_THUMBNAIL_RECIPE,
        },
        label="Mesh geometry and thumbnails",
        applies=lambda: and_(_real(), col(File.file_type).in_(MESH_TYPES)),
        applies_to=lambda file: (
            file.sha256 != SENTINEL_FILE_HASH and file.file_type in MESH_TYPES
        ),
    ),
    DerivativeGroup(
        definition=JobKind.DERIVATIVES_GCODE,
        kinds={
            DerivativeKind.METADATA: GCODE_METADATA_RECIPE,
            DerivativeKind.THUMBNAIL: GCODE_THUMBNAIL_RECIPE,
        },
        label="G-code metadata and thumbnails",
        applies=lambda: and_(_real(), col(File.file_type) == FileType.GCODE),
        applies_to=lambda file: (
            file.sha256 != SENTINEL_FILE_HASH and file.file_type == FileType.GCODE
        ),
    ),
    DerivativeGroup(
        definition=JobKind.DERIVATIVES_TOOLPATH,
        kinds={DerivativeKind.TOOLPATH: TOOLPATH_RECIPE},
        label="Toolpath previews",
        applies=lambda: and_(
            _real(),
            col(File.file_type) == FileType.GCODE,
            or_(
                *(
                    func.lower(File.original_filename).like(f"%{suffix}")
                    for suffix in BINARY_GCODE_SUFFIXES
                )
            ),
        ),
        applies_to=lambda file: (
            file.sha256 != SENTINEL_FILE_HASH and _is_binary_gcode(file)
        ),
    ),
)
_BY_DEFINITION = {candidate.definition: candidate for candidate in GROUPS}


def group(definition: JobKind) -> DerivativeGroup:
    """The producer group behind ``definition``; only derivative kinds have one."""
    try:
        return _BY_DEFINITION[definition]
    except KeyError:
        raise LookupError(f"not_a_derivative_definition:{definition.value}") from None


def is_derivative(definition: JobKind) -> bool:
    return definition in _BY_DEFINITION


def groups_for(file: File) -> list[DerivativeGroup]:
    return [candidate for candidate in GROUPS if candidate.applies_to(file)]


def recipes_for(file: File) -> dict[DerivativeKind, int]:
    """Every kind that applies to ``file``, at its current recipe version."""
    recipes: dict[DerivativeKind, int] = {}
    for candidate in groups_for(file):
        recipes.update(candidate.kinds)
    return recipes


def definitions_for_kind(kind: DerivativeKind) -> list[JobKind]:
    """Every group deriving ``kind``; each kind has at least one."""
    return [candidate.definition for candidate in GROUPS if kind in candidate.kinds]


def all_definitions() -> tuple[JobKind, ...]:
    return tuple(_BY_DEFINITION)
