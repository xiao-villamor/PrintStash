"""Performance fixtures are reproducible, bounded, and retain real format bytes."""

import hashlib
import io
import zipfile

import pytest
import trimesh

from scripts.bench_corpus import generate
from tests.paths import FIXTURES_DIR


class TestBenchmarkCorpus:
    def test_reproduces_identical_archives(self, tmp_path):
        first, second = tmp_path / "first", tmp_path / "second"
        original = generate(first, small_count=2, large_subdivisions=1)
        repeated = generate(second, small_count=2, large_subdivisions=1)
        assert original == repeated
        assert len(original["archives"]) == 7
        for archive in original["archives"]:
            content = (first / archive["name"]).read_bytes()
            assert content == (second / archive["name"]).read_bytes()
            assert hashlib.sha256(content).hexdigest() == archive["sha256"]
            with zipfile.ZipFile(io.BytesIO(content)) as package:
                assert package.testzip() is None
                assert sorted(package.namelist()) == [
                    item["name"] for item in archive["files"]
                ]

    def test_retains_real_slicer_fixtures(self, tmp_path):
        root = tmp_path / "corpus"
        generate(root, small_count=2, large_subdivisions=1)
        with zipfile.ZipFile(root / "gcode.zip") as package:
            for name in package.namelist():
                assert package.read(name) == (FIXTURES_DIR / name).read_bytes()
        with zipfile.ZipFile(root / "small.zip") as package:
            mesh = trimesh.load(
                io.BytesIO(package.read("tetrahedron.stl")), file_type="stl"
            )
            assert len(mesh.faces) == 4
            assert mesh.extents.tolist() == [10, 20, 30]

    def test_preserves_an_existing_destination(self, tmp_path):
        sentinel = tmp_path / "existing"
        sentinel.write_bytes(b"retained baseline")
        with pytest.raises(FileExistsError):
            generate(tmp_path, small_count=2, large_subdivisions=1)
        assert sentinel.read_bytes() == b"retained baseline"
        assert list(tmp_path.iterdir()) == [sentinel]

    @pytest.mark.parametrize(
        "settings",
        [
            {"small_count": 0},
            {"small_count": 129},
            {"large_subdivisions": 0},
            {"large_subdivisions": 8},
        ],
    )
    def test_refuses_unbounded_corpus_work(self, tmp_path, settings):
        output = tmp_path / "corpus"
        with pytest.raises(ValueError, match="declared work bounds"):
            generate(output, **settings)
        assert not output.exists()
