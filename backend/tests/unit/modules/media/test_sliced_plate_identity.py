"""A source plate is associated only when slicer metadata names its G-code."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from app.modules.media.sliced_plate_identity import (
    SlicedPlateIdentity,
    read_sliced_plate_identities,
)


@pytest.fixture
def project(tmp_path: Path):
    def build(
        model: str, sliced: str, members: tuple[str, ...] = ("Metadata/plate_2.gcode",)
    ) -> Path:
        path = tmp_path / "project.3mf"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("Metadata/model_settings.config", model)
            archive.writestr("Metadata/slice_info.config", sliced)
            for member in members:
                archive.writestr(member, "G1 X1")
        return path

    return build


MODEL = """<config><plate><metadata key="plater_id" value="1"/>
<metadata key="plater_name" value="Wall mount"/><metadata key="gcode_file" value="Metadata/plate_2.gcode"/>
</plate></config>"""
SLICE = """<config><plate><metadata key="index" value="1"/><object name="Wall mount.stl" skipped="false"/></plate></config>"""


class TestSlicedPlateIdentity:
    def test_uses_explicit_gcode_association_not_plate_order(self, project):
        assert read_sliced_plate_identities(project(MODEL, SLICE)) == (
            SlicedPlateIdentity(2, "Wall mount", 1),
        )


    def test_missing_gcode_member_cannot_be_an_identity(self, project):
        assert read_sliced_plate_identities(project(MODEL, SLICE, ())) == ()


    def test_duplicate_plate_name_is_ambiguous(self, project):
        model = MODEL.replace(
            "</config>",
            '<plate><metadata key="plater_id" value="2"/><metadata key="plater_name" value="Wall mount"/><metadata key="gcode_file" value="Metadata/plate_3.gcode"/></plate></config>',
        )
        sliced = SLICE.replace(
            "</config>",
            '<plate><metadata key="index" value="2"/><object/></plate></config>',
        )
        assert (
            read_sliced_plate_identities(
                project(model, sliced, ("Metadata/plate_2.gcode", "Metadata/plate_3.gcode"))
            )
            == ()
        )


    def test_duplicate_slice_index_is_ambiguous(self, project):
        sliced = SLICE.replace(
            "</config>",
            '<plate><metadata key="index" value="1"/><object/></plate></config>',
        )
        assert read_sliced_plate_identities(project(MODEL, sliced)) == ()


    def test_skipped_objects_do_not_create_a_match(self, project):
        assert (
            read_sliced_plate_identities(
                project(MODEL, SLICE.replace('skipped="false"', 'skipped="true"'))
            )
            == ()
        )


    @pytest.mark.parametrize(
        "model",
        ["not xml", "<!DOCTYPE config><config/>", "<config/>"],
        ids=["malformed", "dtd", "no-plates"],
    )
    def test_invalid_optional_metadata_has_no_identity(self, project, model):
        assert read_sliced_plate_identities(project(model, SLICE)) == ()
