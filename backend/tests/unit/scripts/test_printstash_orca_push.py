"""The Orca hook turns native slicer facts into one safe upload contract.

Source lineage is only trustworthy when naming, attachment, diagnostics, and the
wire payload all consume the same normalized context. These tests load the shipped
stdlib-only script directly so a refactor cannot leave its downloadable copy behind.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
from types import ModuleType
from zipfile import ZipFile

import pytest

from tests.paths import FIXTURES_DIR, REPO_ROOT


@pytest.fixture(scope="module")
def orca_hook() -> ModuleType:
    script = REPO_ROOT / "scripts" / "printstash_orca_push.py"
    spec = importlib.util.spec_from_file_location("printstash_orca_push", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestExtractNativeContext:
    def test_normalizes_a_single_object_export(self, orca_hook: ModuleType) -> None:
        gcode = FIXTURES_DIR / "real_orca_ender3_benchy.gcode"
        environ = {
            "SLIC3R_PRINTER_MODEL": "Creality Ender-3 V3 SE",
            "SLIC3R_PRINTER_SETTINGS_ID": "Ender profile",
            "SLIC3R_PRINT_SETTINGS_ID": "Balanced Quality",
            "SLIC3R_FILAMENT_TYPE": "PLA",
        }

        context = orca_hook.extract_native_context(gcode, environ)

        assert context == {
            "version": 1,
            "classification": "single_object",
            "source": {
                "filename": "3dbenchy.stl",
                "basename": "3dbenchy",
                "first_object_name": "3dbenchy.stl",
                "object_count": 1,
                "instance_count": 1,
                "plate_name": None,
                "project_name": None,
                "object_labels": [
                    {"name": "3dbenchy.stl", "object_id": "0", "copy_index": 0}
                ],
            },
            "slicer": {"name": "OrcaSlicer", "version": "2.3.2"},
            "printer": {
                "model": "Creality Ender-3 V3 SE",
                "preset": "Ender profile",
            },
            "filaments": [{"type": "PLA", "preset": "Generic PLA Mate"}],
            "process": {"preset": "Balanced Quality"},
            "print_stats": {
                "estimated_time_s": 4296,
                "filament_weight_g": 12.53,
                "filament_length_mm": 4201.91,
                "filament_cost": 0.25,
            },
            "field_sources": {
                "filaments": "runtime",
                "print_stats.estimated_time_s": "gcode_header",
                "print_stats.filament_cost": "gcode_comments",
                "print_stats.filament_length_mm": "gcode_comments",
                "print_stats.filament_weight_g": "gcode_comments",
                "printer.model": "runtime",
                "printer.preset": "runtime",
                "process.preset": "runtime",
                "slicer.name": "gcode_header",
                "slicer.version": "gcode_header",
                "source.first_object_name": "gcode_object_label",
                "source.instance_count": "gcode_object_label",
                "source.object_count": "gcode_object_label",
                "source.object_labels": "gcode_object_label",
                "source.filename": "gcode_object_label",
            },
        }

    def test_prefers_runtime_source_identity_over_markers(
        self, tmp_path, orca_hook: ModuleType
    ) -> None:
        gcode = tmp_path / "export.gcode"
        gcode.write_text(
            "; printstash:input_filename=marker.stl\n"
            "; printstash:num_objects=4\n"
            "; printing object label.stl id:0 copy 0\n",
            encoding="utf-8",
        )

        context = orca_hook.extract_native_context(
            gcode,
            {
                "SLIC3R_INPUT_FILENAME": r"C:\\Models\\runtime.stl",
                "SLIC3R_NUM_OBJECTS": "1",
                "SLIC3R_NUM_INSTANCES": "2",
                "SLIC3R_FIRST_OBJECT_NAME": "Runtime object",
                "SLIC3R_PLATE_NAME": "Runtime plate",
            },
        )

        assert context["classification"] == "single_object"
        assert context["source"] | {"object_labels": []} == {
            "filename": "runtime.stl",
            "basename": "runtime",
            "first_object_name": "Runtime object",
            "object_count": 1,
            "instance_count": 2,
            "plate_name": "Runtime plate",
            "project_name": None,
            "object_labels": [],
        }
        assert context["field_sources"]["source.filename"] == "runtime"

    def test_classifies_copies_of_one_object_as_single_object(
        self, tmp_path, orca_hook: ModuleType
    ) -> None:
        gcode = tmp_path / "copies.gcode"
        gcode.write_text(
            "; printing object bracket.stl id:0 copy 0\n"
            "; printing object bracket.stl id:0 copy 1\n",
            encoding="utf-8",
        )

        context = orca_hook.extract_native_context(gcode, {})

        assert context["classification"] == "single_object"
        assert context["source"]["object_count"] == 1
        assert context["source"]["instance_count"] == 2

    def test_preserves_multi_object_labels_without_source_lineage(
        self, tmp_path, orca_hook: ModuleType
    ) -> None:
        gcode = tmp_path / "plate.gcode"
        gcode.write_text(
            "; printing object bracket.stl id:0 copy 0\n"
            "; printing object stand.stl id:1 copy 0\n",
            encoding="utf-8",
        )

        context = orca_hook.extract_native_context(gcode, {})

        assert context["classification"] == "multi_object"
        assert context["source"]["filename"] is None
        assert [label["name"] for label in context["source"]["object_labels"]] == [
            "bracket.stl",
            "stand.stl",
        ]

    def test_does_not_infer_lineage_from_the_output_filename(
        self, tmp_path, orca_hook: ModuleType
    ) -> None:
        gcode = tmp_path / "looks-like-a-source.stl.gcode"
        gcode.write_text("G28\n", encoding="utf-8")

        context = orca_hook.extract_native_context(
            gcode, {"SLIC3R_PP_OUTPUT_NAME": "actual-source.stl"}
        )

        assert context["classification"] == "unknown"
        assert context["source"]["filename"] is None

    def test_ignores_an_unexpanded_custom_marker(
        self, tmp_path, orca_hook: ModuleType
    ) -> None:
        gcode = tmp_path / "unexpanded.gcode"
        gcode.write_text(
            "; printstash:input_filename={input_filename}\n",
            encoding="utf-8",
        )

        context = orca_hook.extract_native_context(gcode, {})

        assert context["classification"] == "unknown"

    def test_normalizes_a_gcode_3mf_context(
        self, tmp_path, orca_hook: ModuleType
    ) -> None:
        archive_path = tmp_path / "plate.gcode.3mf"
        with ZipFile(archive_path, "w") as archive:
            archive.writestr(
                "Metadata/plate_1.gcode",
                "; printing object bracket.stl id:0 copy 0\n",
            )
            archive.writestr(
                "Metadata/model_settings.config",
                '<config><metadata key="name" value="Workshop bracket"/></config>',
            )

        context = orca_hook.extract_native_context(archive_path, {})

        assert context["classification"] == "single_object"
        assert context["source"]["project_name"] == "Workshop bracket"
        assert context["field_sources"]["source.project_name"] == "project_archive"


class TestHookContract:
    def test_uses_only_python_standard_library_imports(self) -> None:
        script = REPO_ROOT / "scripts" / "printstash_orca_push.py"
        tree = ast.parse(script.read_text(encoding="utf-8"))
        imports = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            (node.module or "").split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )

        assert imports - sys.stdlib_module_names == set()

    def test_submission_identity_is_deterministic(
        self, tmp_path, orca_hook: ModuleType
    ) -> None:
        gcode = tmp_path / "part.gcode"
        gcode.write_bytes(b"G28\n")
        context = {"version": 1, "classification": "unknown"}

        first = orca_hook.submission_id(gcode, context)
        second = orca_hook.submission_id(gcode, context)

        assert first == second

    def test_diagnostic_output_contains_context_but_not_credentials(
        self, tmp_path, orca_hook: ModuleType, monkeypatch
    ) -> None:
        gcode = tmp_path / "part.gcode"
        gcode.write_text(
            "; printing object bracket.stl id:0 copy 0\n", encoding="utf-8"
        )
        diagnostic = tmp_path / "context.json"
        monkeypatch.setattr(
            orca_hook.sys,
            "argv",
            [
                "printstash_orca_push.py",
                "--api-key",
                "psk_super_secret",
                "--diagnostic-output",
                str(diagnostic),
                "--log-path",
                str(tmp_path / "hook.log"),
                str(gcode),
            ],
        )

        result = orca_hook.main()

        payload = diagnostic.read_text(encoding="utf-8")
        assert result == 0
        assert json.loads(payload)["source"]["filename"] == "bracket.stl"
        assert "psk_super_secret" not in payload

    def test_builds_the_wire_payload_from_normalized_context(
        self, tmp_path, orca_hook: ModuleType, monkeypatch
    ) -> None:
        gcode = tmp_path / "part.gcode"
        gcode.write_text(
            "; printing object bracket.stl id:0 copy 0\n", encoding="utf-8"
        )
        captured: dict = {}
        monkeypatch.setattr(
            orca_hook.sys,
            "argv",
            [
                "printstash_orca_push.py",
                "--url",
                "https://printstash.invalid",
                "--username",
                "orca",
                "--api-key",
                "psk_secret",
                "--strict-mapping",
                "--log-path",
                str(tmp_path / "hook.log"),
                str(gcode),
            ],
        )
        monkeypatch.setattr(orca_hook, "login", lambda *_args: "token")

        def capture_push(_url, _token, _gcode, fields) -> bool:
            captured.update(fields)
            return True

        monkeypatch.setattr(orca_hook, "push", capture_push)

        result = orca_hook.main()

        expected_context = orca_hook.extract_native_context(gcode, orca_hook.os.environ)
        assert result == 0
        assert json.loads(captured["native_context"]) == expected_context
        assert captured["submission_id"] == orca_hook.submission_id(
            gcode, expected_context
        )
        assert captured["strict_mapping"] == "true"

    @pytest.mark.parametrize("failure", ["extract", "login", "upload"])
    def test_returns_zero_when_processing_fails(
        self, tmp_path, orca_hook: ModuleType, monkeypatch, failure: str
    ) -> None:
        gcode = tmp_path / "part.gcode"
        gcode.write_text(
            "; printing object bracket.stl id:0 copy 0\n", encoding="utf-8"
        )
        monkeypatch.setattr(
            orca_hook.sys,
            "argv",
            [
                "printstash_orca_push.py",
                "--url",
                "https://printstash.invalid",
                "--username",
                "orca",
                "--api-key",
                "psk_secret",
                "--log-path",
                str(tmp_path / "hook.log"),
                str(gcode),
            ],
        )
        if failure == "extract":
            monkeypatch.setattr(
                orca_hook,
                "extract_native_context",
                lambda *_args: (_ for _ in ()).throw(ValueError("bad metadata")),
            )
        elif failure == "login":
            monkeypatch.setattr(orca_hook, "login", lambda *_args: None)
        else:
            monkeypatch.setattr(orca_hook, "login", lambda *_args: "token")
            monkeypatch.setattr(
                orca_hook,
                "push",
                lambda *_args: (_ for _ in ()).throw(OSError("network down")),
            )

        assert orca_hook.main() == 0


class TestSlicerTempFile:
    """Orca hands the hook a temporary "<final>.pp" copy, never the export itself."""

    def test_uploads_under_the_slicer_output_name(
        self, tmp_path, orca_hook: ModuleType, monkeypatch
    ) -> None:
        gcode = tmp_path / "tmp1234.gcode.pp"
        gcode.write_bytes(b"G28\n")
        monkeypatch.setenv("SLIC3R_PP_OUTPUT_NAME", "/exports/bracket_PLA.gcode")

        body, _ = orca_hook.build_multipart({}, gcode)

        assert b'filename="bracket_PLA.gcode"' in body

    def test_strips_the_pp_suffix_without_an_output_name(
        self, tmp_path, orca_hook: ModuleType
    ) -> None:
        assert orca_hook.output_name(tmp_path / "bracket.gcode.pp", {}) == (
            "bracket.gcode"
        )

    def test_keeps_a_plain_export_name(self, tmp_path, orca_hook: ModuleType) -> None:
        assert orca_hook.output_name(tmp_path / "bracket.gcode", {}) == (
            "bracket.gcode"
        )

    def test_takes_the_basename_of_a_windows_output_name(
        self, tmp_path, orca_hook: ModuleType
    ) -> None:
        environ = {"SLIC3R_PP_OUTPUT_NAME": "C:\\exports\\bracket.gcode"}

        assert orca_hook.output_name(tmp_path / "tmp.pp", environ) == ("bracket.gcode")

    def test_reads_a_gcode_3mf_under_the_slicer_temp_name(
        self, tmp_path, orca_hook: ModuleType
    ) -> None:
        archive_path = tmp_path / "tmp1234.gcode.3mf.pp"
        with ZipFile(archive_path, "w") as archive:
            archive.writestr(
                "Metadata/plate_1.gcode",
                "; printing object bracket.stl id:0 copy 0\n",
            )

        context = orca_hook.extract_native_context(archive_path, {})

        assert context["classification"] == "single_object"

    @staticmethod
    def _capture_uploads(orca_hook: ModuleType, monkeypatch) -> list[bytes]:
        sent: list[bytes] = []

        class _Response:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def read(self) -> bytes:
                return b"{}"

        def fake_urlopen(request, timeout):
            sent.append(request.data)
            return _Response()

        monkeypatch.setattr(orca_hook.urlrequest, "urlopen", fake_urlopen)
        return sent

    def test_push_uploads_under_the_slicer_output_name(
        self, tmp_path, orca_hook: ModuleType, monkeypatch
    ) -> None:
        gcode = tmp_path / "tmp1234.gcode.pp"
        gcode.write_bytes(b"G28\n")
        monkeypatch.setenv("SLIC3R_PP_OUTPUT_NAME", "/exports/bracket_PLA.gcode")
        sent = self._capture_uploads(orca_hook, monkeypatch)

        assert orca_hook.push("https://printstash.invalid", "token", gcode, {})
        assert b'filename="bracket_PLA.gcode"' in sent[0]

    def test_main_uploads_the_slicer_output_name(
        self, tmp_path, orca_hook: ModuleType, monkeypatch
    ) -> None:
        gcode = tmp_path / "tmp1234.gcode.pp"
        gcode.write_text(
            "; printing object bracket.stl id:0 copy 0\n", encoding="utf-8"
        )
        monkeypatch.setenv("SLIC3R_PP_OUTPUT_NAME", "/exports/bracket_PLA.gcode")
        monkeypatch.setattr(
            orca_hook.sys,
            "argv",
            [
                "printstash_orca_push.py",
                "--url",
                "https://printstash.invalid",
                "--username",
                "orca",
                "--api-key",
                "psk_secret",
                "--log-path",
                str(tmp_path / "hook.log"),
                str(gcode),
            ],
        )
        monkeypatch.setattr(orca_hook, "login", lambda *_args: "token")
        sent = self._capture_uploads(orca_hook, monkeypatch)

        assert orca_hook.main() == 0
        assert b'filename="bracket_PLA.gcode"' in sent[0]
