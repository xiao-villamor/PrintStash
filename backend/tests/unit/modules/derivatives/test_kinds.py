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

METADATA, THUMBNAIL, TOOLPATH, VIEWER_STL = (
    DerivativeKind.METADATA,
    DerivativeKind.THUMBNAIL,
    DerivativeKind.TOOLPATH,
    DerivativeKind.VIEWER_STL,
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

    def test_a_3mf_gets_its_own_mesh_recipes(self) -> None:
        # 3MF scenes are read by lib3mf in single precision; only they re-derive.
        assert kinds.recipes_for(_file(FileType.THREE_MF)) == {
            METADATA: kinds.THREE_MF_GEOMETRY_RECIPE,
            THUMBNAIL: kinds.THREE_MF_THUMBNAIL_RECIPE,
        }

    def test_a_step_gets_its_own_mesh_recipes(self) -> None:
        # STEP is tessellated at 0.05 mm; only STEP Artifacts re-derive.
        assert kinds.recipes_for(_file(FileType.STEP)) == {
            METADATA: kinds.STEP_GEOMETRY_RECIPE,
            THUMBNAIL: kinds.STEP_THUMBNAIL_RECIPE,
        }

    @pytest.mark.parametrize(
        "file_type",
        [FileType.STL, FileType.OBJ],
        ids=lambda file_type: file_type.value,
    )
    def test_other_meshes_keep_the_shared_mesh_recipes(
        self, file_type: FileType
    ) -> None:
        assert kinds.recipes_for(_file(file_type)) == {
            METADATA: kinds.MESH_GEOMETRY_RECIPE,
            THUMBNAIL: kinds.MESH_THUMBNAIL_RECIPE,
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
        assert kinds.group(JobKind.DERIVATIVES_MESH).kinds == (METADATA, THUMBNAIL)

    def test_versions_a_3mf_viewer_preview_on_its_own(self) -> None:
        viewer = kinds.group(JobKind.DERIVATIVES_VIEWER_STL)

        assert viewer.recipes_for(FileType.THREE_MF) == {
            VIEWER_STL: kinds.THREE_MF_VIEWER_STL_RECIPE
        }

    def test_versions_a_step_viewer_preview_on_its_own(self) -> None:
        viewer = kinds.group(JobKind.DERIVATIVES_VIEWER_STL)

        assert viewer.recipes_for(FileType.STEP) == {
            VIEWER_STL: kinds.STEP_VIEWER_STL_RECIPE
        }

    def test_keeps_the_shared_viewer_preview_recipe_for_other_meshes(self) -> None:
        viewer = kinds.group(JobKind.DERIVATIVES_VIEWER_STL)

        assert viewer.recipes_for(FileType.OBJ) == {VIEWER_STL: kinds.VIEWER_STL_RECIPE}

    def test_refuses_a_format_recipe_for_a_kind_outside_the_group(self) -> None:
        with pytest.raises(ValueError, match="format recipe"):
            kinds.DerivativeGroup(
                definition=JobKind.DERIVATIVES_TOOLPATH,
                recipes={TOOLPATH: 1},
                label="toolpaths",
                applies=lambda: None,
                applies_to=lambda _file: False,
                format_recipes={FileType.GCODE: {METADATA: 2}},
            )

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
