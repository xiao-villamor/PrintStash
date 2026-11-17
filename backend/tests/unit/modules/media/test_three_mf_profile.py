"""Project settings are bounded evidence from a file, never measured print results."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from app.modules.media.three_mf_profile import ProfileError, read_profile


def package(tmp_path, settings=None, *, raw=None, slices=None, duplicate=False):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        if settings is not None or raw is not None:
            value = raw if raw is not None else json.dumps(settings)
            archive.writestr("Metadata/project_settings.config", value)
            if duplicate:
                with pytest.warns(UserWarning):
                    archive.writestr("Metadata/project_settings.config", value)
        if slices is not None:
            archive.writestr("Metadata/slice_info.config", slices)
    path = tmp_path / "project.3mf"
    path.write_bytes(output.getvalue())
    return path


class TestReadProfile:
    def test_reads_explicit_project_settings(self, tmp_path):
        path = package(
            tmp_path,
            {
                "print_settings_id": "0.20mm Standard",
                "printer_model": "Bambu Lab P1S",
                "filament_type": ["PLA", "PETG"],
                "nozzle_diameter": ["0.4"],
                "layer_height": "0.2",
                "wall_loops": "2",
                "sparse_infill_density": "15%",
                "machine_start_gcode": "never project arbitrary settings",
            },
        )
        result = read_profile(path)
        assert result is not None
        assert result.name == "0.20mm Standard"
        assert result.printer == "Bambu Lab P1S"
        assert result.materials == ("PLA", "PETG")
        assert result.nozzle_diameters_mm == (0.4,)
        assert result.layer_height_mm == 0.2
        assert result.wall_loops == 2
        assert result.infill_percent == 15
        assert "gcode" not in repr(result)

    def test_preserves_per_plate_estimates(self, tmp_path):
        path = package(
            tmp_path,
            {},
            slices="""<config><plate>
        <metadata key="index" value="2"/><metadata key="prediction" value="600"/>
        <metadata key="weight" value="12.5"/></plate><plate>
        <metadata key="index" value="7"/><metadata key="prediction" value="90"/>
        </plate></config>""",
        )
        result = read_profile(path)
        assert [
            (p.number, p.estimated_seconds, p.filament_grams) for p in result.plates
        ] == [(2, 600, 12.5), (7, 90, None)]

    def test_leaves_absent_fields_unknown(self, tmp_path):
        result = read_profile(package(tmp_path, {}))
        assert result.name is None
        assert result.printer is None
        assert result.materials == ()
        assert result.plates == ()
        assert result.layer_height_mm is None

    def test_skips_unsupported_dialect(self, tmp_path):
        assert read_profile(package(tmp_path)) is None

    def test_rejects_duplicate_members(self, tmp_path):
        with pytest.raises(ProfileError, match="project_profile_malformed"):
            read_profile(package(tmp_path, {}, duplicate=True))

    def test_rejects_duplicate_json_keys(self, tmp_path):
        with pytest.raises(ProfileError, match="project_profile_malformed"):
            read_profile(
                package(tmp_path, raw='{"layer_height":"0.2","layer_height":"0.3"}')
            )

    @pytest.mark.parametrize(
        "raw",
        [
            "[]",
            "{",
            '{"layer_height":NaN}',
            '{"layer_height":true}',
            '{"filament_type":"PLA"}',
            '{"wall_loops":"2.5"}',
        ],
        ids=[
            "array",
            "invalid-json",
            "nan",
            "boolean",
            "material-shape",
            "fractional-walls",
        ],
    )
    def test_rejects_malformed_settings(self, tmp_path, raw):
        with pytest.raises(ProfileError, match="project_profile_malformed"):
            read_profile(package(tmp_path, raw=raw))

    def test_rejects_dtd(self, tmp_path):
        with pytest.raises(ProfileError, match="project_profile_malformed"):
            read_profile(
                package(
                    tmp_path,
                    {},
                    slices='<!DOCTYPE config [<!ENTITY x SYSTEM "file:///etc/passwd">]><config/>',
                )
            )

    def test_rejects_duplicate_plate_numbers(self, tmp_path):
        with pytest.raises(ProfileError, match="project_profile_malformed"):
            read_profile(
                package(
                    tmp_path,
                    {},
                    slices='<config><plate><metadata key="index" value="1"/></plate><plate><metadata key="index" value="1"/></plate></config>',
                )
            )

    def test_rejects_large_settings(self, tmp_path):
        with pytest.raises(ProfileError, match="project_profile_limits"):
            read_profile(package(tmp_path, raw=b" " * (2 * 1024 * 1024 + 1)))

    def test_preserves_original_bytes(self, tmp_path):
        path = package(tmp_path, {"layer_height": "0.2"})
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        read_profile(path)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before

    @pytest.mark.parametrize(
        "settings",
        [
            {"print_settings_id": 4},
            {"printer_model": "x" * 513},
            {"filament_type": ["PLA"] * 65},
            {"nozzle_diameter": ["0.4"] * 65},
            {"layer_height": "x" * 513},
            {"infill": 5, "sparse_infill_density": "101%"},
            {"layer_height": "-0.1"},
            {"layer_height": "no-number"},
        ],
        ids=[
            "text-type",
            "long-name",
            "materials-limit",
            "nozzles-limit",
            "long-number",
            "infill-range",
            "negative",
            "not-numeric",
        ],
    )
    def test_refuses_invalid_known_fields(self, tmp_path, settings):
        with pytest.raises(ProfileError):
            read_profile(package(tmp_path, settings))

    def test_preserves_empty_optional_settings(self, tmp_path):
        result = read_profile(
            package(
                tmp_path,
                {
                    "print_settings_id": "",
                    "layer_height": "",
                    "filament_type": ["", "PLA"],
                    "nozzle_diameter": ["", "0.4"],
                },
            )
        )
        assert result.name is None
        assert result.layer_height_mm is None
        assert result.materials == ("PLA",)
        assert result.nozzle_diameters_mm == (0.4,)

    @pytest.mark.parametrize(
        "slices",
        [
            "<broken",
            "<other/>",
            '<config><plate><metadata key="index" value="0"/></plate></config>',
            "<config><plate/></config>",
            '<config><plate><metadata key="index" value="1.5"/></plate></config>',
            '<config><plate><metadata key="index" value="1"/><metadata key="weight" value="2"/><metadata key="weight" value="3"/></plate></config>',
        ],
        ids=[
            "xml",
            "root",
            "zero",
            "missing-index",
            "fractional-index",
            "duplicate-estimate",
        ],
    )
    def test_refuses_ambiguous_plate_estimates(self, tmp_path, slices):
        with pytest.raises(ProfileError, match="project_profile_malformed"):
            read_profile(package(tmp_path, {}, slices=slices))

    def test_bounds_plate_count(self, tmp_path):
        slices = "<config>" + "<plate/>" * 65 + "</config>"
        with pytest.raises(ProfileError, match="project_profile_limits"):
            read_profile(package(tmp_path, {}, slices=slices))

    def test_rejects_excessive_compression(self, tmp_path):
        path = tmp_path / "bomb.3mf"
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("Metadata/project_settings.config", b" " * 500_000)
        with pytest.raises(ProfileError, match="project_profile_limits"):
            read_profile(path)

    def test_bounds_archive_entries(self, tmp_path):
        from app.modules.media import three_mf_package

        path = tmp_path / "entries.3mf"
        with zipfile.ZipFile(path, "w") as archive:
            for index in range(three_mf_package.MAX_ENTRIES + 1):
                archive.writestr(str(index), b"")
        with pytest.raises(ProfileError, match="project_profile_limits"):
            read_profile(path)

    def test_refuses_an_invalid_zip(self, tmp_path):
        path = tmp_path / "invalid.3mf"
        path.write_bytes(b"invalid")
        with pytest.raises(ProfileError, match="project_profile_malformed"):
            read_profile(path)
