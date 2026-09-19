"""The benchmark proves import completion against a fresh real application."""

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scripts.bench_import import run
from tests.factories.geometry import tetrahedron


class TestImportBenchmark:
    @pytest.mark.parametrize(
        "dialect",
        [
            "sqlite",
            pytest.param("postgres", marks=pytest.mark.postgres),
            pytest.param("postgres-managed", marks=pytest.mark.postgres),
        ],
    )
    def test_benchmarks_a_complete_import(self, tmp_path, dialect):
        source = tetrahedron().export(file_type="stl")
        gcode = (
            Path(__file__).parents[1] / "fixtures/real_prusa_mk4_spatula.gcode"
        ).read_bytes()
        archive = tmp_path / "models.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("part.stl", source)
            package.writestr("spatula.gcode", gcode)
        output = tmp_path / "report.json"

        postgres_admin_url = None
        if dialect == "postgres-managed":
            from sqlalchemy.engine import make_url

            from tests.containers import postgres_url

            postgres_admin_url = (
                make_url(postgres_url())
                .set(database="postgres")
                .render_as_string(hide_password=False)
            )
            dialect = "postgres"
        report = run(
            archive,
            output,
            120,
            database=dialect,
            postgres_admin_url=postgres_admin_url,
        )

        assert report["status"]["state"] == "completed"
        assert report["catalog"] == sorted(
            (hashlib.sha256(data).hexdigest(), len(data)) for data in (source, gcode)
        )
        geometry = {row[0]: row for row in report["geometry_catalog"]}
        assert geometry[hashlib.sha256(source).hexdigest()][-1] == 4
        facts = report["metadata_catalog"]["metadata"]
        assert len(facts) == 2
        by_source = {row["source_sha256"]: row for row in facts}
        mesh_facts = by_source[hashlib.sha256(source).hexdigest()]
        assert mesh_facts["triangle_count"] == 4
        slicer_facts = by_source[hashlib.sha256(gcode).hexdigest()]
        assert slicer_facts["slicer_name"] == "PrusaSlicer"
        assert slicer_facts["estimated_time_s"] == 1598
        assert slicer_facts["layer_height_mm"] == 0.15
        assert slicer_facts["material_type"] == "PLA"
        assert all("file_id" not in row and "created_at" not in row for row in facts)
        requirements = report["metadata_catalog"]["artifact_material_requirements"]
        assert requirements == [
            {
                "source_sha256": hashlib.sha256(gcode).hexdigest(),
                "tool_index": 0,
                "material_type": "PLA",
                "color_hex": "#FF8000",
            }
        ]
        assert {row[0]: row[-1] for row in report["preview_pixel_catalog"]} == {
            hashlib.sha256(source).hexdigest(): "ready",
            hashlib.sha256(gcode).hexdigest(): "ready",
        }
        assert report["database"]["backend"] == (
            "postgresql" if dialect == "postgres" else "sqlite"
        )
        assert json.loads(output.read_text())["database"] == report["database"]

    @pytest.mark.parametrize(
        "dialect", ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)]
    )
    def test_records_gcode_without_embedded_previews(self, tmp_path, dialect):
        fixtures = Path(__file__).parents[1] / "fixtures"
        orca = (fixtures / "real_orca_ender3_benchy.gcode").read_bytes()
        binary = (fixtures / "bgcode/prusaslicer.bgcode").read_bytes()
        archive = tmp_path / "gcode.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.writestr("benchy.gcode", orca)
            package.writestr("prusa.bgcode", binary)

        report = run(archive, tmp_path / "report.json", 120, database=dialect)

        assert report["status"]["state"] == "completed"
        assert report["failed_enrichment"]["thumbnail_generations"] == 0
        assert report["preview_outcome_catalog"] == sorted(
            (
                hashlib.sha256(data).hexdigest(),
                "GCODE",
                "failed",
                "no_embedded_thumbnail",
            )
            for data in (orca, binary)
        )
        assert {
            row["slicer_name"]: row["estimated_time_s"]
            for row in report["metadata_catalog"]["metadata"]
        } == {"OrcaSlicer": 4296, "PrusaSlicer": 221}
        assert "PermissionError" not in (tmp_path / "report.server.log").read_text()
