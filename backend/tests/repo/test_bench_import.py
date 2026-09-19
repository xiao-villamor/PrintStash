"""A compression experiment must still reject any changed preview pixels."""

import httpx
import pytest

from scripts.bench_import import (
    compare_databases,
    compare_fingerprints,
    compare_metadata,
    compare_preview_outcomes,
    compare_previews,
    environment_record,
)


class TestCompareDatabases:
    def test_accepts_matching_database_versions(self):
        database = {"backend": "postgresql", "server_version": [16, 10]}
        compare_databases({"database": database}, {"database": database})

    @pytest.mark.parametrize(
        "changed",
        [
            {"backend": "sqlite", "server_version": [3, 46, 1]},
            {"backend": "postgresql", "server_version": [17, 0]},
            None,
        ],
        ids=["backend", "version", "missing"],
    )
    def test_refuses_incompatible_database_evidence(self, changed):
        reference = {"backend": "postgresql", "server_version": [16, 10]}
        with pytest.raises(SystemExit, match="database backend or version differs"):
            compare_databases({"database": reference}, {"database": changed})

    def test_requires_reference_database_evidence(self):
        with pytest.raises(SystemExit, match="database backend or version differs"):
            compare_databases({}, {})


class TestEnvironmentRecord:
    def test_records_a_source_archive_without_git(self, tmp_path):
        record = environment_record(tmp_path)
        assert record["git_revision"] is None
        assert record["dependency_versions"]["numpy"] is not None
        assert "server startup" in record["timing_excludes"]
        assert record["cache_condition"].startswith("uncontrolled")

    def test_records_an_image_without_the_git_executable(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", str(tmp_path))
        record = environment_record(tmp_path)
        assert record["git_revision"] is None
        assert record["dependency_versions"]["numpy"] is not None


class TestCompleteSetup:
    @staticmethod
    def response(status_code: int) -> httpx.Response:
        return httpx.Response(
            status_code,
            json={"access_token": "benchmark-token"},
        )

    def test_retries_one_rate_limited_setup_before_timing(self, monkeypatch):
        from scripts import bench_import

        responses = [self.response(429), self.response(201)]
        requests = []
        sleeps = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return responses.pop(0)

        monkeypatch.setattr(bench_import.time, "sleep", sleeps.append)

        with httpx.Client(
            base_url="http://127.0.0.1", transport=httpx.MockTransport(handler)
        ) as client:
            result = bench_import.complete_setup(client, "csrf", {"username": "bench"})

        assert result == {"access_token": "benchmark-token"}
        assert sleeps == [61]
        assert responses == []
        assert [request.url.path for request in requests] == [
            "/api/v1/setup",
            "/api/v1/setup",
        ]
        assert all(
            request.headers["X-PrintStash-Setup-CSRF"] == "csrf" for request in requests
        )

    def test_stops_after_the_bounded_setup_retry(self, monkeypatch):
        from scripts import bench_import

        responses = [self.response(429), self.response(429)]
        sleeps = []

        def handler(request: httpx.Request) -> httpx.Response:
            return responses.pop(0)

        monkeypatch.setattr(bench_import.time, "sleep", sleeps.append)

        with (
            httpx.Client(
                base_url="http://127.0.0.1", transport=httpx.MockTransport(handler)
            ) as client,
            pytest.raises(httpx.HTTPStatusError),
        ):
            bench_import.complete_setup(client, "csrf", {"username": "bench"})

        assert sleeps == [61]
        assert responses == []


class TestCompareFingerprints:
    def test_accepts_equal_final_output(self):
        before = {"fingerprint_catalog": [["source", 0, "recipe", "ready", "hash"]]}
        after = {"fingerprint_catalog": [("source", 0, "recipe", "ready", "hash")]}

        compare_fingerprints(before, after)

    def test_rejects_changed_final_output(self):
        with pytest.raises(SystemExit, match="final similarity fingerprints differ"):
            compare_fingerprints(
                {"fingerprint_catalog": [["source", 0, "recipe", "ready", "before"]]},
                {"fingerprint_catalog": [["source", 0, "recipe", "ready", "after"]]},
            )

    def test_requires_final_output_evidence(self):
        with pytest.raises(SystemExit, match="final fingerprint catalog is missing"):
            compare_fingerprints({}, {"fingerprint_catalog": []})


class TestComparePreviews:
    def test_allows_changed_lossless_compression(self) -> None:
        pixels = [["source", "rgba", 320, 240, "ready"]]
        before = {
            "preview_catalog": [["source", "old"]],
            "preview_pixel_catalog": pixels,
        }
        after = {
            "preview_catalog": [["source", "new"]],
            "preview_pixel_catalog": pixels,
        }

        compare_previews(before, after, mode="pixels")

    def test_rejects_changed_bytes_by_default(self) -> None:
        with pytest.raises(SystemExit, match="preview bytes differ"):
            compare_previews({"preview_catalog": ["old"]}, {"preview_catalog": ["new"]})

    @pytest.mark.parametrize(
        "changed",
        [
            ["source", "changed", 320, 240, "ready"],
            ["source", "rgba", 240, 320, "ready"],
            ["source", "rgba", 320, 240, "failed"],
        ],
        ids=["pixels", "dimensions", "state"],
    )
    def test_rejects_changed_preview(self, changed: list) -> None:
        with pytest.raises(SystemExit, match="preview pixels differ"):
            compare_previews(
                {"preview_pixel_catalog": [["source", "rgba", 320, 240, "ready"]]},
                {"preview_pixel_catalog": [changed]},
                mode="pixels",
            )

    def test_rejects_missing_reference_pixels(self) -> None:
        with pytest.raises(SystemExit, match="no decoded pixel catalog"):
            compare_previews({}, {"preview_pixel_catalog": []}, mode="pixels")


class TestBenchmarkArguments:
    @pytest.mark.parametrize("database", ["sqlite", "postgres"])
    def test_rejects_invalid_external_server_configuration(
        self, monkeypatch, tmp_path, database
    ):
        import sys

        from scripts import bench_import

        monkeypatch.delenv("PRINTSTASH_M00_TEST_ADMIN_URL", raising=False)
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "bench_import",
                str(tmp_path / "absent.zip"),
                "--output",
                str(tmp_path / "result.json"),
                "--database",
                database,
                "--postgres-admin-url-env",
                "PRINTSTASH_M00_TEST_ADMIN_URL",
            ],
        )
        with pytest.raises(SystemExit) as error:
            bench_import.main()
        assert error.value.code == 2
        assert not (tmp_path / "result.json").exists()

    def test_supports_the_documented_script_entry_point(self, tmp_path):
        import os
        import subprocess
        import sys
        from pathlib import Path

        script = Path(__file__).resolve().parents[2] / "scripts" / "bench_import.py"
        environment = {
            key: value for key, value in os.environ.items() if key != "PYTHONPATH"
        }
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        assert "--database {sqlite,postgres}" in result.stdout

    @pytest.mark.parametrize("flag", ["--renderer", "--loader", "--geometry"])
    def test_rejects_removed_engine_flags(self, monkeypatch, flag, tmp_path):
        import sys

        from scripts import bench_import

        monkeypatch.setattr(
            sys,
            "argv",
            [
                "bench_import",
                str(tmp_path / "models.zip"),
                "--output",
                str(tmp_path / "result.json"),
                flag,
                "python",
            ],
        )
        with pytest.raises(SystemExit) as exit:
            bench_import.main()
        assert exit.value.code == 2
        assert not (tmp_path / "result.json").exists()


class TestLatencySummary:
    def test_reports_the_navigation_probe_distribution(self):
        from scripts.bench_import import latency_summary

        assert latency_summary(
            [{"library_ms": 100}, {"library_ms": 20}, {"library_ms": 50}]
        ) == {"samples": 3, "median_ms": 50, "p95_ms": 100, "max_ms": 100}


class TestCompareMetadata:
    def test_rejects_lost_slicer_facts(self):
        with pytest.raises(SystemExit, match="parsed metadata differs"):
            compare_metadata(
                {"metadata_catalog": {"metadata": [{"estimated_time_s": 90}]}},
                {"metadata_catalog": {"metadata": [{"estimated_time_s": None}]}},
            )

    def test_accepts_identical_facts(self):
        facts = {
            "metadata": [{"estimated_time_s": 90}],
            "artifact_material_requirements": [],
        }
        compare_metadata({"metadata_catalog": facts}, {"metadata_catalog": facts})

    def test_refuses_missing_evidence(self):
        with pytest.raises(SystemExit, match="parsed metadata evidence is missing"):
            compare_metadata({}, {})


class TestComparePreviewOutcomes:
    def test_accepts_equal_unavailable_previews(self):
        outcome = ["source", "GCODE", "failed", "no_embedded_thumbnail"]
        assert (
            compare_preview_outcomes(
                {"preview_outcome_catalog": [outcome]},
                {"preview_outcome_catalog": [tuple(outcome)]},
            )
            is None
        )

    def test_rejects_changed_failure_reason(self):
        with pytest.raises(SystemExit, match="preview outcomes differ"):
            compare_preview_outcomes(
                {
                    "preview_outcome_catalog": [
                        ["source", "GCODE", "failed", "no_embedded_thumbnail"]
                    ]
                },
                {
                    "preview_outcome_catalog": [
                        ["source", "GCODE", "failed", "enrichment_failed"]
                    ]
                },
            )

    def test_requires_outcome_evidence(self):
        with pytest.raises(SystemExit, match="preview outcome evidence is missing"):
            compare_preview_outcomes({}, {})
