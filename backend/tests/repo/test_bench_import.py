"""A compression experiment must still reject any changed preview pixels."""

import pytest

from scripts.bench_import import compare_previews, environment_record


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


@pytest.mark.parametrize("flag", ["--renderer", "--loader", "--geometry"])
def test_benchmark_rejects_removed_engine_flags(monkeypatch, flag, tmp_path):
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
