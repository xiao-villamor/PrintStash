"""Unit tests for the G-code metadata parser."""

from __future__ import annotations

from pathlib import Path

from app.services.gcode_parser import parse, parse_duration


class TestParseDuration:
    def test_parse_hms(self) -> None:
        assert parse_duration("1h 23m 45s") == 5025

    def test_parse_days(self) -> None:
        assert parse_duration("2d 4h") == 187200

    def test_parse_seconds(self) -> None:
        assert parse_duration("90s") == 90

    def test_parse_plain_integer(self) -> None:
        assert parse_duration("3600") == 3600

    def test_parse_float(self) -> None:
        assert parse_duration("123.5") == 123

    def test_parse_empty(self) -> None:
        assert parse_duration("") is None

    def test_parse_nonsense(self) -> None:
        assert parse_duration("not a duration") is None

    def test_parse_zero(self) -> None:
        assert parse_duration("0") == 0
        assert parse_duration("0h 0m 0s") == 0

    def test_parse_fractional_hours(self) -> None:
        # Regression: "1.5h" must be 90 minutes, not 5h (it used to match the
        # bare "5h" inside "1.5h" because the value group only took integers).
        assert parse_duration("1.5h") == 5400

    def test_parse_fractional_minutes(self) -> None:
        assert parse_duration("0.5m") == 30

    def test_parse_negative_is_rejected(self) -> None:
        # A negative print time is never valid telemetry.
        assert parse_duration("-5") is None

    def test_parse_normal_mode_suffix(self) -> None:
        # PrusaSlicer's "(normal mode)" line still parses its trailing value.
        assert parse_duration("1h 5m 30s") == 3930

    def test_parse_whitespace_only(self) -> None:
        assert parse_duration("   ") is None


class TestParse:
    def test_parse_sample_gcode(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "sample.gcode"
        result = parse(fixture)
        assert result["slicer_name"] == "OrcaSlicer"
        assert result["slicer_version"] == "OrcaSlicer 1.9.0"
        assert result["printer_model"] == "Ender-3 V3 SE"
        assert result["nozzle_diameter_mm"] == 0.4
        assert result["layer_height_mm"] == 0.2
        assert result["first_layer_height_mm"] == 0.24
        assert result["infill_percent"] == 15.0
        assert result["wall_loops"] == 3
        assert result["top_shell_layers"] == 4
        assert result["bottom_shell_layers"] == 3
        assert result["support_material"] is False
        assert result["nozzle_temperature_c"] == 215.0
        assert result["bed_temperature_c"] == 60.0
        assert result["estimated_time_s"] == 5025
        assert result["filament_weight_g"] == 12.5
        assert result["filament_length_mm"] == 4200.0
        assert result["filament_cost"] == 0.35
        assert result["material_type"] == "PLA"
        assert result["material_brand"] == "Generic PLA"
        assert result["printer_preset_name"] is None

    def test_parse_prusaslicer_fixture(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "prusaslicer_sample.gcode"
        result = parse(fixture)
        assert result["slicer_name"] == "PrusaSlicer"
        assert result["printer_model"] == "MK4 Input Shaper 0.4 nozzle"
        assert result["printer_preset_name"] == "MK4 Input Shaper 0.4 nozzle"
        assert result["infill_percent"] == 20.0
        assert result["estimated_time_s"] == 3372
        assert result["filament_length_mm"] == 3350.2
        assert result["filament_weight_g"] == 9.7
        assert result["material_type"] == "PETG"

    def test_parse_bambu_studio_fixture(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "bambu_studio_sample.gcode"
        result = parse(fixture)
        assert result["slicer_name"] == "BambuStudio"
        assert result["printer_model"] == "Bambu Lab P1S 0.4 nozzle"
        assert result["layer_height_mm"] == 0.16
        assert result["estimated_time_s"] == 7445
        assert result["material_type"] == "PLA"

    def test_parse_cura_fixture(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "cura_sample.gcode"
        result = parse(fixture)
        assert result["slicer_name"] == "Cura"
        assert result["slicer_version"] == "Cura_SteamEngine 5.6.0"
        assert result["printer_model"] == "Voron Switchwire"
        assert result["nozzle_diameter_mm"] == 0.6
        assert result["layer_height_mm"] == 0.28
        assert result["infill_percent"] == 35.0
        assert result["estimated_time_s"] == 3661
        assert result["filament_length_mm"] == 1234.0
        assert result["material_type"] == "TPU"

    def test_parse_klipper_orca_fixture(self) -> None:
        fixture = Path(__file__).parent / "fixtures" / "klipper_orca_sample.gcode"
        result = parse(fixture)
        assert result["slicer_name"] == "OrcaSlicer"
        assert result["printer_model"] == "Voron 2.4 350 Klipper"
        assert result["nozzle_diameter_mm"] == 0.4
        assert result["layer_height_mm"] == 0.24
        assert result["estimated_time_s"] == 11520

    def test_parse_real_orca_benchy_fixture(self) -> None:
        # Self-sourced from real OrcaSlicer 2.3.2 output (Ender-3 V3 SE benchy),
        # trimmed to head+tail. Locks in the real field set, not synthetic values.
        fixture = Path(__file__).parent / "fixtures" / "real_orca_ender3_benchy.gcode"
        result = parse(fixture)
        assert result["slicer_name"] == "OrcaSlicer"
        assert result["slicer_version"] == "OrcaSlicer 2.3.2"
        assert result["printer_model"] == "Creality Ender-3 V3 SE"
        assert result["nozzle_diameter_mm"] == 0.4
        assert result["layer_height_mm"] == 0.2
        assert result["infill_percent"] == 15.0
        # Regression: Orca names the heated bed "hot_plate_temp", not "bed_temperature".
        assert result["bed_temperature_c"] == 60.0
        assert result["nozzle_temperature_c"] == 210.0
        assert result["estimated_time_s"] == 4296
        # Regression: a "Filament used:4.20m" comment in Orca's Marlin start-gcode
        # must NOT scale the real "[mm]" length by 1000 (4201.91, not 4_201_910).
        assert result["filament_length_mm"] == 4201.91
        assert result["filament_weight_g"] == 12.53
        assert result["material_type"] == "PLA"

    def test_parse_real_prusa_spatula_fixture(self) -> None:
        # Self-sourced from real PrusaSlicer 2.6.1 output (MK4 Input Shaper).
        fixture = Path(__file__).parent / "fixtures" / "real_prusa_mk4_spatula.gcode"
        result = parse(fixture)
        assert result["slicer_name"] == "PrusaSlicer"
        assert result["slicer_version"] == "PrusaSlicer 2.6.1+win64"
        assert result["printer_model"] == "MK4IS"
        # Regression: PrusaSlicer writes "fill_density = 15%" (underscore), which
        # the old "fill density" (space) pattern missed.
        assert result["infill_percent"] == 15.0
        assert result["bed_temperature_c"] == 60.0
        assert result["nozzle_temperature_c"] == 225.0
        assert result["layer_height_mm"] == 0.15
        assert result["estimated_time_s"] == 1598
        assert result["filament_length_mm"] == 3535.89
        assert result["material_type"] == "PLA"

    def test_orca_metres_comment_does_not_scale_mm_length(self, tmp_path: Path) -> None:
        # Regression for the ×1000 bug: when an explicit "[mm]" length is present,
        # a stray "Filament used: N m" comment elsewhere must be ignored.
        p = tmp_path / "orca.gcode"
        p.write_text(
            "; generated by OrcaSlicer 2.3.2\n"
            ";Filament used:4.20m\n"
            "; filament used [mm] = 4201.91\n"
        )
        assert parse(p)["filament_length_mm"] == 4201.91

    def test_hot_plate_temp_does_not_match_initial_layer_variant(
        self, tmp_path: Path
    ) -> None:
        # "hot_plate_temp = 60" is the bed temp; "hot_plate_temp_initial_layer = 55"
        # must not be picked up by the steady-state pattern.
        p = tmp_path / "orca.gcode"
        p.write_text(
            "; generated by OrcaSlicer 2.3.2\n"
            "; hot_plate_temp_initial_layer = 55\n"
            "; hot_plate_temp = 60\n"
        )
        assert parse(p)["bed_temperature_c"] == 60.0

    def test_prusa_fill_density_underscore(self, tmp_path: Path) -> None:
        p = tmp_path / "prusa.gcode"
        p.write_text("; generated by PrusaSlicer 2.6.1\n; fill_density = 15%\n")
        assert parse(p)["infill_percent"] == 15.0

    def test_parse_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        result = parse(tmp_path / "nonexistent.gcode")
        assert result["slicer_name"] is None
        assert result["estimated_time_s"] is None

    def test_empty_file_returns_all_none(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.gcode"
        p.write_bytes(b"")
        result = parse(p)
        assert all(v is None for v in result.values())

    def test_no_comments_returns_all_none(self, tmp_path: Path) -> None:
        p = tmp_path / "bare.gcode"
        p.write_text("G28\nG1 X0 Y0 Z0\nG1 X10 Y10 E5\n")
        result = parse(p)
        assert all(v is None for v in result.values())

    def test_cura_metres_with_padded_colon(self, tmp_path: Path) -> None:
        # Regression: Cura reports a *metres* length; the mm conversion keyed off
        # the literal "filament used:" substring and silently skipped when the
        # colon was padded ("Filament used : 1.5m"), storing 1.5 mm not 1500 mm.
        p = tmp_path / "cura.gcode"
        p.write_text("; generated by Cura_SteamEngine 5.6.0\n;Filament used : 1.5m\n")
        assert parse(p)["filament_length_mm"] == 1500.0

    def test_prusaslicer_mm_length_not_scaled(self, tmp_path: Path) -> None:
        # PrusaSlicer already reports mm — the metres path must not fire.
        p = tmp_path / "prusa.gcode"
        p.write_text(
            "; generated by PrusaSlicer 2.7.0\n; filament used [mm] = 3350.2\n"
        )
        assert parse(p)["filament_length_mm"] == 3350.2

    def test_bed_temperature_does_not_leak_into_nozzle(self, tmp_path: Path) -> None:
        # The generic "temperature" fallback must not capture "bed_temperature"
        # when no nozzle temperature line exists.
        p = tmp_path / "temps.gcode"
        p.write_text("; generated by PrusaSlicer 2.7.0\n; bed_temperature = 60\n")
        result = parse(p)
        assert result["bed_temperature_c"] == 60.0
        assert result["nozzle_temperature_c"] is None

    def test_support_material_truthy_variants(self, tmp_path: Path) -> None:
        p = tmp_path / "sup.gcode"
        p.write_text("; generated by OrcaSlicer 1.9.0\n; enable_support = 1\n")
        assert parse(p)["support_material"] is True

    def test_material_type_strips_trailing_comment(self, tmp_path: Path) -> None:
        p = tmp_path / "mat.gcode"
        p.write_text("; generated by OrcaSlicer 1.9.0\n; filament_type = PLA; second\n")
        assert parse(p)["material_type"] == "PLA"

    def test_crlf_line_endings(self, tmp_path: Path) -> None:
        p = tmp_path / "crlf.gcode"
        p.write_bytes(
            b"; generated by OrcaSlicer 1.9.0\r\n; layer_height = 0.2\r\n"
        )
        result = parse(p)
        assert result["slicer_name"] == "OrcaSlicer"
        assert result["layer_height_mm"] == 0.2

    def test_invalid_utf8_does_not_crash(self, tmp_path: Path) -> None:
        p = tmp_path / "binary.gcode"
        p.write_bytes(b"; generated by OrcaSlicer 1.9.0\n; \xff\xfe layer_height = 0.2\n")
        result = parse(p)
        assert result["slicer_name"] == "OrcaSlicer"

    def test_tail_window_is_scanned(self, tmp_path: Path) -> None:
        # Slicers write the filament/time summary at the *end*; ensure a large
        # body between head and tail doesn't hide tail metadata.
        p = tmp_path / "big.gcode"
        head = "; generated by PrusaSlicer 2.7.0\n; layer_height = 0.2\n"
        body = "G1 X1 Y1\n" * 20000  # > 64 KiB of motion
        tail = "; total filament used [g] = 42.5\n"
        p.write_text(head + body + tail)
        result = parse(p)
        assert result["layer_height_mm"] == 0.2  # from head
        assert result["filament_weight_g"] == 42.5  # from tail
