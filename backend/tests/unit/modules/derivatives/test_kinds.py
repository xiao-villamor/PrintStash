"""Which derivative kinds apply to an Artifact, and which producer makes each.

An Artifact belongs to one group by its type: a mesh gets geometry and a
rendered thumbnail, G-code gets slicer metadata and its embedded thumbnail,
and binary G-code additionally gets a converted toolpath. An external-library
sentinel row has no bytes of its own and gets nothing.
"""

from __future__ import annotations

import pytest

from app.db.models import SENTINEL_FILE_HASH, DerivativeKind, File, FileType, JobKind
from app.modules.derivatives import kinds
from tests.factories.library import detached_file

METADATA, THUMBNAIL, TOOLPATH = (
    DerivativeKind.METADATA,
    DerivativeKind.THUMBNAIL,
    DerivativeKind.TOOLPATH,
)


def _file(file_type: FileType, name: str = "part", sha: str = "a" * 64) -> File:
    return detached_file(original_filename=name, file_type=file_type, sha256=sha)


class TestRecipesFor:
    @pytest.mark.parametrize(
        "file_type", [FileType.STL, FileType.THREE_MF, FileType.OBJ, FileType.STEP]
    )
    def test_a_mesh_gets_every_mesh_kind(self, file_type: FileType) -> None:
        assert set(kinds.recipes_for(_file(file_type))) == {METADATA, THUMBNAIL}

    def test_plain_gcode_gets_what_its_header_holds(self) -> None:
        assert set(kinds.recipes_for(_file(FileType.GCODE, "plate.gcode"))) == {
            METADATA,
            THUMBNAIL,
        }

    @pytest.mark.parametrize("name", ["plate.bgcode", "PLATE.BGC"])
    def test_binary_gcode_also_gets_a_toolpath(self, name: str) -> None:
        assert set(kinds.recipes_for(_file(FileType.GCODE, name))) == {
            METADATA,
            THUMBNAIL,
            TOOLPATH,
        }

    def test_a_sentinel_row_gets_nothing(self) -> None:
        assert kinds.recipes_for(_file(FileType.STL, sha=SENTINEL_FILE_HASH)) == {}

    def test_a_dxf_drawing_gets_nothing_yet(self) -> None:
        # Kept as its original bytes; no drawing preview renderer exists, so
        # no derivative is owed (it never shows as pending or failed).
        assert kinds.recipes_for(_file(FileType.DXF, "plate.dxf")) == {}


class TestGroups:
    def test_names_the_producers_of_each_kind(self) -> None:
        assert kinds.definitions_for_kind(TOOLPATH) == [JobKind.DERIVATIVES_TOOLPATH]
        assert set(kinds.definitions_for_kind(THUMBNAIL)) == {
            JobKind.DERIVATIVES_MESH,
            JobKind.DERIVATIVES_GCODE,
        }

    @pytest.mark.parametrize("kind", list(DerivativeKind))
    def test_every_kind_has_a_producer(self, kind: DerivativeKind) -> None:
        assert kinds.definitions_for_kind(kind)

    def test_lists_a_groups_kinds(self) -> None:
        assert list(kinds.group(JobKind.DERIVATIVES_MESH).kinds) == [
            METADATA,
            THUMBNAIL,
        ]

    def test_a_definition_without_a_group_is_refused(self) -> None:
        # Only derivative definitions have a group; asking for any other is a
        # caller's bug, not an empty answer.
        assert not kinds.is_derivative(JobKind.SOURCES_SCAN)
        with pytest.raises(LookupError, match="not_a_derivative_definition"):
            kinds.group(JobKind.SOURCES_SCAN)

    def test_every_group_is_a_definition(self) -> None:
        assert set(kinds.all_definitions()) == {
            JobKind.DERIVATIVES_MESH,
            JobKind.DERIVATIVES_GCODE,
            JobKind.DERIVATIVES_TOOLPATH,
            JobKind.DERIVATIVES_VIEWER_STL,
        }
