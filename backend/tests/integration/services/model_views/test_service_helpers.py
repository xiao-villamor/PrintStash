"""Unit coverage for service-layer helpers that previously had none.

Groups the small, pure, high-value functions behind the import pipeline, the
Moonraker client, the read-model cost maths and the external-library scheduler.
These are the bits most prone to silent edge-case regressions, so they get
direct tests rather than only incidental coverage through the API suite.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlmodel import Session

from app.db.models import (
    ExternalLibrary,
    ExternalLibraryCollectionMode,
    FilamentProfile,
    FileType,
    Metadata,
    PrinterProfile,
)
from app.services import importer as imp
from app.services import model_views as mv
from app.services.external_library import _collection_path_for, _walk, is_due
from app.services.ingestion import _collision_safe_path
from app.services.moonraker import MoonrakerClient
from tests.factories import (
    build_file,
    build_model,
    detached_model,
)


# --------------------------------------------------------------------------- #
# importer — Content-Disposition filename parsing
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, cd: str) -> None:
        self.headers = {"content-disposition": cd}


class TestContentDispositionName:
    @pytest.mark.parametrize(
        "header, expected",
        [
            ('attachment; filename="benchy.stl"', "benchy.stl"),
            ("attachment; filename=benchy.stl", "benchy.stl"),
            ("attachment; filename=benchy.stl; size=10", "benchy.stl"),
            # Regression: a ';' inside the quoted value is part of the name,
            # not a parameter separator (used to truncate to "a").
            ('attachment; filename="a;b.stl"', "a;b.stl"),
            # A path in the filename is reduced to its basename.
            ('attachment; filename="/etc/passwd"', "passwd"),
            ("attachment; filename=a%20b.stl", "a b.stl"),
        ],
    )
    def test_parses(self, header: str, expected: str) -> None:
        assert imp._content_disposition_name(_Resp(header)) == expected

    @pytest.mark.parametrize("header", ["inline", "", "attachment"])
    def test_no_filename_is_none(self, header: str) -> None:
        assert imp._content_disposition_name(_Resp(header)) is None


class TestFilenameFromUrl:
    @pytest.mark.parametrize(
        "url, expected",
        [
            ("https://x.com/path/benchy.stl", "benchy.stl"),
            ("https://x.com/a%20b.stl", "a b.stl"),
            ("https://x.com/", "download"),  # falls back
            ("https://x.com", "download"),
        ],
    )
    def test_basename_or_fallback(self, url: str, expected: str) -> None:
        assert imp._filename_from_url(url) == expected


# --------------------------------------------------------------------------- #
# importer — archive entry safety (zip-slip)
# --------------------------------------------------------------------------- #
class TestSafeEntryName:
    @pytest.mark.parametrize("name", ["model.stl", "sub/dir/model.stl", "a/b/c.3mf"])
    def test_accepts_relative_paths(self, name: str) -> None:
        assert imp._safe_entry_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "",
            "dir/",  # directory entry
            "/etc/passwd",  # absolute posix
            "\\windows\\x",  # absolute windows
            "../evil.stl",  # posix traversal
            "a/../../evil.stl",  # traversal mid-path
            "..\\..\\evil.stl",  # regression: backslash traversal on posix
            "C:\\evil.stl",  # drive letter
        ],
    )
    def test_rejects_unsafe(self, name: str) -> None:
        assert imp._safe_entry_name(name) is False


# --------------------------------------------------------------------------- #
# importer — SSRF URL validation (pure scheme/host checks; no DNS)
# --------------------------------------------------------------------------- #
class TestValidatePublicUrl:
    @pytest.mark.parametrize(
        "url, code",
        [
            ("ftp://example.com/x", "url_scheme_not_allowed"),
            ("file:///etc/passwd", "url_scheme_not_allowed"),
            ("notaurl", "url_scheme_not_allowed"),
            ("http:///nohost", "url_host_missing"),
        ],
    )
    def test_rejects_bad_scheme_or_host(self, url: str, code: str) -> None:
        with pytest.raises(imp.ImportError_) as exc:
            imp.validate_public_url(url)
        assert str(exc.value) == code


class TestCollectionForArchive:
    def test_nests_under_parent(self) -> None:
        assert (
            imp._collection_for_archive("Functional", "Brackets.zip")
            == "Functional/Brackets"
        )

    def test_no_parent_uses_archive_stem(self) -> None:
        assert imp._collection_for_archive(None, "MyPack.zip") == "MyPack"

    def test_blank_parent_is_ignored(self) -> None:
        assert imp._collection_for_archive("   ", "Pack.zip") == "Pack"

    def test_trailing_slash_on_parent_collapses(self) -> None:
        assert imp._collection_for_archive("A/B/", "Pack.zip") == "A/B/Pack"


# --------------------------------------------------------------------------- #
# moonraker — websocket URL derivation
# --------------------------------------------------------------------------- #
class TestMoonrakerWsUrl:
    @pytest.mark.parametrize(
        "base, expected",
        [
            ("https://printer:7125", "wss://printer:7125/websocket"),
            ("http://printer:7125", "ws://printer:7125/websocket"),
            ("http://printer:7125/", "ws://printer:7125/websocket"),  # trailing slash
            ("http://10.0.0.5", "ws://10.0.0.5/websocket"),
        ],
    )
    def test_scheme_swap(self, base: str, expected: str) -> None:
        assert MoonrakerClient(base)._ws_url() == expected


# --------------------------------------------------------------------------- #
# model_views — filament profile matching + cost maths
# --------------------------------------------------------------------------- #
def _profiles() -> list[FilamentProfile]:
    return [
        FilamentProfile(
            name="Hatchbox PLA",
            material_type="PLA",
            material_brand="Hatchbox",
            cost_per_kg=20.0,
        ),
        FilamentProfile(
            name="Generic PETG",
            material_type="PETG",
            material_brand=None,
            cost_per_kg=25.0,
        ),
        FilamentProfile(
            name="No Cost PLA", material_type="PLA", material_brand="NoCost"
        ),
    ]


class TestMatchingFilamentProfile:
    def test_exact_name_match_case_insensitive(self) -> None:
        md = Metadata(file_id=1, material_brand="hatchbox pla")
        assert mv._matching_filament_profile(_profiles(), md).name == "Hatchbox PLA"

    def test_matches_on_type_together_with_brand(self) -> None:
        md = Metadata(file_id=1, material_type="PLA", material_brand="Hatchbox")
        assert mv._matching_filament_profile(_profiles(), md).name == "Hatchbox PLA"

    def test_type_only_matches_brandless_profile(self) -> None:
        md = Metadata(file_id=1, material_type="PETG")
        assert mv._matching_filament_profile(_profiles(), md).name == "Generic PETG"

    def test_no_match_returns_none(self) -> None:
        md = Metadata(file_id=1, material_type="ABS")
        assert mv._matching_filament_profile(_profiles(), md) is None


class TestFilamentCostForGrams:
    def test_cost_scales_per_kg(self) -> None:
        md = Metadata(file_id=1, material_type="PLA", material_brand="Hatchbox")
        # 100 g of a 20/kg filament => 2.00.
        assert mv.filament_cost_for_grams(_profiles(), md, 100.0) == 2.0

    def test_none_grams_or_metadata(self) -> None:
        md = Metadata(file_id=1, material_type="PLA", material_brand="Hatchbox")
        assert mv.filament_cost_for_grams(_profiles(), md, None) is None
        assert mv.filament_cost_for_grams(_profiles(), None, 100.0) is None

    def test_profile_without_cost_returns_none(self) -> None:
        md = Metadata(file_id=1, material_type="PLA", material_brand="NoCost")
        assert mv.filament_cost_for_grams(_profiles(), md, 100.0) is None


# --------------------------------------------------------------------------- #
# model_views — CSV cell rendering (zero preservation)
# --------------------------------------------------------------------------- #
class TestCsvCell:
    @pytest.mark.parametrize("value", [0, 0.0, False, "x", "0"])
    def test_keeps_real_values_including_zero(self, value) -> None:
        # Regression: a falsy-but-real value (0 % infill, 0 °C bed) must survive.
        assert mv._csv_cell(value) == value

    def test_none_becomes_empty(self) -> None:
        assert mv._csv_cell(None) == ""

    def test_export_csv_preserves_zero_metadata(self) -> None:
        # End-to-end: a vase-mode print (0 % infill, 0 °C bed, 0 top layers)
        # must export "0", not blank, while genuinely-absent fields stay blank.
        import csv
        import io

        payload = {
            "counts": {"models": 1, "files": 1},
            "models": [
                {
                    "id": 1,
                    "name": "Vase",
                    "slug": "vase",
                    "source_url": None,
                    "collection": None,
                    "tags": [],
                    "files": [
                        {
                            "id": 10,
                            "file_type": "gcode",
                            "version": 1,
                            "original_filename": "v.gcode",
                            "size_bytes": 0,
                            "sha256": "abc",
                            "is_recommended": True,
                            "uploaded_at": "2024-01-01",
                            "metadata": {
                                "infill_percent": 0,
                                "bed_temperature_c": 0,
                                "top_shell_layers": 0,
                                "slicer_name": None,
                            },
                        }
                    ],
                }
            ],
        }
        row = next(csv.DictReader(io.StringIO(mv.export_csv(payload))))
        assert row["infill_percent"] == "0"
        assert row["bed_temperature_c"] == "0"
        assert row["top_shell_layers"] == "0"
        assert row["size_bytes"] == "0"
        assert row["slicer_name"] == ""  # truly absent stays blank


# --------------------------------------------------------------------------- #
# model_views — thumbnail URL resolution
# --------------------------------------------------------------------------- #
class TestThumbUrl:
    def test_prefers_thumbnail_file_id(self) -> None:
        model = detached_model(thumbnail_file_id=7, thumbnail_path="99.png")
        assert mv.thumb_url(model) == "/api/v1/files/7/thumbnail"

    def test_falls_back_to_legacy_digit_stem_path(self) -> None:
        model = detached_model(thumbnail_path="uploads/42.png")
        assert mv.thumb_url(model) == "/api/v1/files/42/thumbnail"

    def test_non_digit_legacy_stem_returns_none(self) -> None:
        model = detached_model(thumbnail_path="uploads/legacy.png")
        assert mv.thumb_url(model) is None

    def test_no_thumbnail_at_all_returns_none(self) -> None:
        model = detached_model()
        assert mv.thumb_url(model) is None


# --------------------------------------------------------------------------- #
# model_views — profile usage counters (DB-backed matching loops)
# --------------------------------------------------------------------------- #
class TestFilamentProfileUsage:
    def test_counts_live_files_matching_each_profile(self, db_session: Session) -> None:
        db_session.add_all(_profiles())
        model = build_model(db_session, name="m", slug="m", hash="b" * 64)
        f1 = build_file(
            db_session,
            model,
            path="a.gcode",
            filename="a.gcode",
            file_type=FileType.GCODE,
            size_bytes=1,
            sha256="1" * 64,
        )
        f2 = build_file(
            db_session,
            model,
            path="b.gcode",
            filename="b.gcode",
            file_type=FileType.GCODE,
            size_bytes=1,
            sha256="2" * 64,
            version=2,
        )
        db_session.add(f1)
        db_session.commit()
        db_session.refresh(f1)
        db_session.refresh(f2)
        db_session.add(
            Metadata(file_id=f1.id, material_type="PLA", material_brand="Hatchbox")
        )
        db_session.add(
            Metadata(file_id=f2.id, material_type="PLA", material_brand="Hatchbox")
        )
        db_session.commit()

        usage = mv.filament_profile_usage(db_session)

        hatchbox = next(
            p
            for p in db_session.exec(
                __import__("sqlmodel")
                .select(FilamentProfile)
                .where(FilamentProfile.name == "Hatchbox PLA")
            ).all()
        )
        assert usage[hatchbox.id] == 2


class TestPrinterProfileUsage:
    def test_counts_live_files_matching_printer_model_or_preset_name(
        self, db_session: Session
    ) -> None:
        profile = PrinterProfile(name="Voron 2.4 350", printer_model="Voron 2.4")
        db_session.add(profile)
        db_session.commit()
        db_session.refresh(profile)
        model = build_model(db_session, name="pm", slug="pm", hash="c" * 64)
        f1 = build_file(
            db_session,
            model,
            path="a.gcode",
            filename="a.gcode",
            file_type=FileType.GCODE,
            size_bytes=1,
            sha256="3" * 64,
        )
        db_session.add(Metadata(file_id=f1.id, printer_model="Voron 2.4"))
        db_session.commit()

        usage = mv.printer_profile_usage(db_session)

        assert usage[profile.id] == 1

    def test_blank_printer_model_is_skipped(self, db_session: Session) -> None:
        model = build_model(db_session, name="pm2", slug="pm2", hash="d" * 64)
        f1 = build_file(
            db_session,
            model,
            path="a.gcode",
            filename="a.gcode",
            file_type=FileType.GCODE,
            size_bytes=1,
            sha256="4" * 64,
        )
        db_session.add(Metadata(file_id=f1.id, printer_model=None))
        db_session.commit()

        # No exception, and nothing counted for an unmatched/blank model.
        assert mv.printer_profile_usage(db_session) == {}


# --------------------------------------------------------------------------- #
# model_views — metadata_read loading its own profile list when none is given
# --------------------------------------------------------------------------- #
class TestMetadataReadLoadsProfiles:
    def test_loads_profiles_itself_when_not_provided(self, db_session: Session) -> None:
        db_session.add_all(_profiles())
        db_session.commit()
        md = Metadata(
            file_id=1,
            material_type="PLA",
            material_brand="Hatchbox",
            filament_weight_g=100.0,
        )

        result = mv.metadata_read(db_session, md)

        assert result.filament_cost == 2.0


# --------------------------------------------------------------------------- #
# ingestion — collision-safe NAS write path
# --------------------------------------------------------------------------- #
class TestCollisionSafePath:
    def test_returns_name_when_free(self, tmp_path: Path) -> None:
        assert _collision_safe_path(tmp_path, "model.stl").name == "model.stl"

    def test_appends_next_free_suffix(self, tmp_path: Path) -> None:
        (tmp_path / "model.stl").write_text("x")
        assert _collision_safe_path(tmp_path, "model.stl").name == "model-2.stl"
        (tmp_path / "model-2.stl").write_text("x")
        assert _collision_safe_path(tmp_path, "model.stl").name == "model-3.stl"

    def test_keeps_suffix_when_disambiguating(self, tmp_path: Path) -> None:
        (tmp_path / "part.3mf").write_text("x")
        out = _collision_safe_path(tmp_path, "part.3mf")
        assert out.suffix == ".3mf" and out.stem == "part-2"

    def test_handles_extensionless_name(self, tmp_path: Path) -> None:
        (tmp_path / "README").write_text("x")
        assert _collision_safe_path(tmp_path, "README").name == "README-2"


# --------------------------------------------------------------------------- #
# external_library — filesystem walk + mirror-mode collection mapping
# --------------------------------------------------------------------------- #
class TestWalk:
    def test_includes_supported_suffixes_case_insensitively(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "a.stl").write_text("x")
        (tmp_path / "b.STL").write_text("x")  # uppercase ext still counts
        (tmp_path / "c.txt").write_text("x")  # unsupported, ignored
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "d.3mf").write_text("x")  # recurses

        names = {Path(k).name for k in _walk(tmp_path)}
        assert names == {"a.stl", "b.STL", "d.3mf"}

    def test_empty_dir_yields_nothing(self, tmp_path: Path) -> None:
        assert _walk(tmp_path) == {}


class TestCollectionPathForMirror:
    def _lib(self, root: str) -> ExternalLibrary:
        return ExternalLibrary(
            name="L",
            root_path=root,
            collection_mode=ExternalLibraryCollectionMode.MIRROR,
        )

    def test_nested_dirs_become_collection_path(self) -> None:
        lib = self._lib("/nas/lib")
        out = _collection_path_for(
            None, lib, Path("/nas/lib/Functional/Brackets/x.stl")
        )
        assert out == "Functional/Brackets"

    def test_root_level_file_has_no_collection(self) -> None:
        lib = self._lib("/nas/lib")
        assert _collection_path_for(None, lib, Path("/nas/lib/x.stl")) is None

    def test_file_outside_root_returns_none(self) -> None:
        lib = self._lib("/nas/lib")
        assert _collection_path_for(None, lib, Path("/elsewhere/x.stl")) is None

    def test_trailing_slash_on_root_is_tolerated(self) -> None:
        lib = self._lib("/nas/lib/")
        out = _collection_path_for(None, lib, Path("/nas/lib/Toys/x.stl"))
        assert out == "Toys"


# --------------------------------------------------------------------------- #
# external_library — cron scheduler
# --------------------------------------------------------------------------- #
class TestIsDue:
    NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_invalid_schedule_never_due(self) -> None:
        assert is_due("not a cron", None, self.NOW) is False

    def test_empty_schedule_never_due(self) -> None:
        assert is_due("", None, self.NOW) is False

    def test_never_scanned_with_valid_schedule_is_due(self) -> None:
        assert is_due("0 * * * *", None, self.NOW) is True

    def test_due_when_next_fire_has_passed(self) -> None:
        last = datetime(2024, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        assert is_due("*/30 * * * *", last, self.NOW) is True

    def test_not_due_before_next_fire(self) -> None:
        last = datetime(2024, 1, 1, 11, 59, 0, tzinfo=timezone.utc)
        assert is_due("0 0 * * *", last, self.NOW) is False
