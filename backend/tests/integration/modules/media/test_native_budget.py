"""Source preflight stays bounded and cannot guess unknown geometry complexity."""

import hashlib
import io
import json
import logging
import os
import zipfile
from pathlib import Path

import pytest

from app.modules.media import mesh_policy, native_budget, three_mf_bounds
from app.runtime.native_admission import LocalResourcePool, Resources
from app.runtime.native_runtime import admit
from tests.factories.content import ascii_stl, binary_stl, zip_bytes
from tests.factories.geometry import tetrahedron, three_mf

THUMBNAIL_WORK = native_budget.RasterWork(640, 480, 1, native_budget.RasterCodec.WEBP)


def _estimates(caplog) -> list[dict]:
    return [
        json.loads(record.getMessage().removeprefix("mesh_estimate "))
        for record in caplog.records
        if record.getMessage().startswith("mesh_estimate ")
    ]


@pytest.fixture
def without_lib3mf(monkeypatch):
    """A platform without lib3mf: the worker reads every 3MF as XML."""
    monkeypatch.setattr(three_mf_bounds, "lib3mf_available", lambda: False)


class TestFaceCapacity:
    def test_weighs_a_lib3mf_scene_by_its_own_weight(self):
        assert native_budget.face_capacity(600 * 1000, ".3mf", lib3mf=True) == 1000

    def test_weighs_an_xml_scene_by_the_xml_weight(self):
        assert native_budget.face_capacity(3600 * 1000, ".3mf") == 1000

    @pytest.mark.parametrize("suffix", [".step", ".stp", ".STEP"], ids=str)
    def test_weighs_a_step_mesh_by_the_step_weight(self, suffix):
        assert native_budget.face_capacity(1500 * 1000, suffix) == 1000

    def test_ignores_the_lib3mf_weight_outside_3mf(self):
        assert native_budget.face_capacity(3000 * 1000, ".stl", lib3mf=True) == 1000


class TestOptimisticStepClaim:
    def test_claims_the_optimistic_allowance_for_a_thumbnail(self):
        assert native_budget.optimistic_step_claim(
            Resources(2, 4 * 1024**3), THUMBNAIL_WORK
        ) == Resources(1, native_budget.STEP_OPTIMISTIC_MEMORY)

    def test_claims_the_optimistic_allowance_for_geometry(self):
        assert native_budget.optimistic_step_claim(
            Resources(2, 4 * 1024**3), native_budget.GeometryWork()
        ) == Resources(1, native_budget.STEP_OPTIMISTIC_MEMORY)

    def test_adds_the_workspace_of_a_larger_raster(self):
        raster = native_budget.RasterWork(1280, 960, 1, native_budget.RasterCodec.WEBP)

        claim = native_budget.optimistic_step_claim(Resources(2, 4 * 1024**3), raster)

        assert claim == Resources(
            1,
            native_budget.STEP_OPTIMISTIC_MEMORY
            + (1280 * 960 - native_budget.QUALIFIED_RASTER_PIXELS)
            * native_budget.RASTER_PIXEL_BYTES,
        )

    def test_keeps_fingerprint_analysis_out_of_the_optimistic_claim(self):
        assert (
            native_budget.optimistic_step_claim(
                Resources(2, 4 * 1024**3), native_budget.AnalysisWork(THUMBNAIL_WORK)
            )
            is None
        )

    @pytest.mark.parametrize(
        "pool_bytes", [1024**3, 512 * 1024**2], ids=["equal", "smaller"]
    )
    def test_declines_a_pool_no_larger_than_the_optimistic_claim(self, pool_bytes):
        assert (
            native_budget.optimistic_step_claim(
                Resources(2, pool_bytes), THUMBNAIL_WORK
            )
            is None
        )


class TestEstimateSources:
    @pytest.mark.parametrize(
        "payload",
        [ascii_stl(triangles=1000), binary_stl(triangles=1000), b"solid invalid\n"],
        ids=["ascii", "binary", "invalid"],
    )
    def test_minimum_claim_needs_only_a_header(
        self, tmp_path, count_source_reads, payload
    ):
        source = tmp_path / "source.stl"
        source.write_bytes(payload)
        reads = count_source_reads(source)
        pool = Resources(4, 4 * 1024**3)

        claim = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "stl"),)
        )

        assert claim == Resources(1, native_budget.MIN_NATIVE_MEMORY)
        assert sum(reads) <= 84

    def test_large_ascii_source_retains_its_weighted_claim(self, tmp_path):
        source = tmp_path / "source.stl"
        source.write_bytes(ascii_stl(triangles=200_000))

        claim = native_budget.estimate_sources(
            Resources(4, 4 * 1024**3), (native_budget.MeshSource(source, "stl"),)
        )

        assert claim == Resources(1, 600_000_000)

    def test_combined_sources_keep_their_aggregate_claim(self, tmp_path):
        first = tmp_path / "first.stl"
        second = tmp_path / "second.stl"
        first.write_bytes(ascii_stl(triangles=100_000))
        second.write_bytes(ascii_stl(triangles=100_000))

        claim = native_budget.estimate_sources(
            Resources(4, 4 * 1024**3),
            (
                native_budget.MeshSource(first, "stl"),
                native_budget.MeshSource(second, "stl"),
            ),
        )

        assert claim == Resources(1, 600_000_000)

    @pytest.mark.parametrize(
        "indices", ["1 2 3", "-3 -2 -1"], ids=["positive", "relative"]
    )
    def test_counts_plain_obj_triangles(self, tmp_path, indices):
        source = tmp_path / "mesh.obj"
        source.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf " + indices + "\n")

        claim = native_budget.estimate_sources(
            Resources(4, 4 * 1024**3), (native_budget.MeshSource(source, "obj"),)
        )

        assert claim == Resources(1, native_budget.MIN_NATIVE_MEMORY)

    def test_bounds_obj_preflight_cpu(self, tmp_path, monkeypatch):
        source = tmp_path / "mesh.obj"
        source.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        clock = iter([0.0, 0.251])
        monkeypatch.setattr(native_budget.time, "thread_time", lambda: next(clock))

        claim = native_budget.estimate_sources(
            Resources(4, 4 * 1024**3), (native_budget.MeshSource(source, "obj"),)
        )

        assert claim == Resources(1, 4 * 1024**3)

    def test_counts_slower_bounded_text(self, tmp_path, monkeypatch):
        source = tmp_path / "mesh.obj"
        source.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        clock = iter([0.0, 0.0, 0.15, 0.16])
        monkeypatch.setattr(native_budget.time, "monotonic", lambda: next(clock))

        claim = native_budget.estimate_sources(
            Resources(4, 4 * 1024**3), (native_budget.MeshSource(source, "obj"),)
        )

        assert claim == Resources(1, native_budget.MIN_NATIVE_MEMORY)

    def test_reads_binary_count_through_a_stable_descriptor(self, tmp_path):
        source = tmp_path / "mesh.stl"
        source.write_bytes(binary_stl(triangles=2))
        pool = Resources(4, 4 * 1024**3)

        with source.open("rb") as stream:
            alias = Path(f"/proc/self/fd/{stream.fileno()}")
            amount = native_budget.estimate_sources(
                pool, (native_budget.MeshSource(alias, "stl"),)
            )

        assert amount == Resources(1, 512 * 1024**2)

    @pytest.mark.parametrize(
        "file_type,payload",
        [
            pytest.param("3mf", b"PK", id="archive"),
            pytest.param("obj", b"v 0 0 0\n", id="polygon"),
        ],
    )
    def test_unmeasurable_input_reserves_whole_pool(self, tmp_path, file_type, payload):
        source = tmp_path / "source"
        source.write_bytes(payload)
        pool = Resources(4, 4 * 1024**3)

        assert native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, file_type),)
        ) == Resources(1, pool.bytes)

    @pytest.mark.parametrize(
        "file_type,payload",
        [
            pytest.param("stl", ascii_stl(triangles=2), id="ascii-stl"),
            pytest.param("obj", b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", id="obj"),
        ],
    )
    def test_validated_text_source_uses_a_weighted_claim(
        self, tmp_path, file_type, payload
    ):
        source = tmp_path / "source"
        source.write_bytes(payload)
        pool = Resources(4, 4 * 1024**3)

        amount = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, file_type),)
        )

        assert amount == Resources(1, 512 * 1024**2)

    @pytest.mark.parametrize(
        "face",
        [
            pytest.param(b"f 1//1 2//1 3//1", id="normals"),
            pytest.param(b"f 1/1 2/1 3/1", id="texture-coordinates"),
            pytest.param(b"f 1/1/1 2/1/1 3/1/1", id="all-indices"),
            pytest.param(b"f -3/-1/-1 -2/-1/-1 -1/-1/-1", id="relative-indices"),
        ],
    )
    def test_bounds_obj_coordinate_buffers_before_weighting(self, tmp_path, face):
        source = tmp_path / "mesh.obj"
        source.write_bytes(
            b"v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvn 0 0 1\n" + face + b"\n"
        )
        pool = Resources(4, 4 * 1024**3)

        amount = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "obj"),)
        )

        assert amount == Resources(1, 512 * 1024**2)

    @pytest.mark.parametrize(
        "face",
        [
            pytest.param(b"f 1///1 2///1 3///1", id="extra-index-channel"),
            pytest.param(b"f 1/ 2/ 3/", id="missing-texture-index"),
            pytest.param(b"f 1// 2// 3//", id="missing-normal-index"),
            pytest.param(b"f 1//2 2//2 3//2", id="out-of-range-normal"),
        ],
    )
    def test_index_validation_remains_with_the_guarded_obj_loader(self, tmp_path, face):
        source = tmp_path / "mesh.obj"
        source.write_bytes(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nvn 0 0 1\n" + face + b"\n")
        pool = Resources(4, 4 * 1024**3)

        amount = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "obj"),)
        )

        assert amount == Resources(1, native_budget.MIN_NATIVE_MEMORY)

    def test_embedded_material_reference_retains_whole_pool(self, tmp_path):
        source = tmp_path / "mesh.obj"
        source.write_bytes(
            b"# mtllib external.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
        )
        pool = Resources(4, 4 * 1024**3)

        amount = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "obj"),)
        )

        assert amount == Resources(1, pool.bytes)

    def test_text_estimation_preserves_analysis_work(self, tmp_path):
        source = tmp_path / "source.stl"
        source.write_bytes(ascii_stl(triangles=2))
        pool = Resources(4, 4 * 1024**3)

        amount = native_budget.estimate_sources(
            pool,
            (native_budget.MeshSource(source, "stl"),),
            work=native_budget.AnalysisWork(),
        )

        assert amount == Resources(1, 1024**3)

    @pytest.mark.parametrize(
        "tail,weighted",
        [
            pytest.param(b"f 1 2 4\n", True, id="missing-vertex"),
            pytest.param(b"f -4 -2 -1\n", True, id="missing-relative-vertex"),
            pytest.param(b"f 0 2 3\n", True, id="zero-index"),
            pytest.param(b"f 1//1 2//1 3//1\n", True, id="missing-normal"),
            pytest.param(b"f 1 2\n", False, id="incomplete-face"),
            pytest.param(b"f 1 2 3\nmtllib external.mtl\n", False, id="material"),
            pytest.param(b"f 1 2 \\\n3\n", False, id="continuation"),
            pytest.param(b"v 0 0 0\nf 1 2 3\n", False, id="excess-vertices"),
            pytest.param(b"\xff", False, id="nonascii"),
        ],
    )
    def test_obj_admission_bounds_storage_without_certifying_indices(
        self, tmp_path, tail, weighted
    ):
        source = tmp_path / "source.obj"
        source.write_bytes(b"v 0 0 0\nv 1 0 0\nv 0 1 0\n" + tail)
        pool = Resources(4, 4 * 1024**3)
        amount = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "obj"),)
        )
        expected = native_budget.MIN_NATIVE_MEMORY if weighted else pool.bytes
        assert amount == Resources(1, expected)

    def test_numeric_validation_belongs_to_the_guarded_loader(self, tmp_path):
        source = tmp_path / "source.obj"
        source.write_bytes(b"v nan 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
        pool = Resources(4, 4 * 1024**3)
        assert native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "obj"),)
        ) == Resources(1, native_budget.MIN_NATIVE_MEMORY)

    def test_oversized_text_remains_unknown_without_loading(self, tmp_path):
        file_type = "obj"
        source = tmp_path / "source"
        with source.open("wb") as stream:
            stream.truncate(native_budget.TEXT_PREFLIGHT_BYTES + 1)
        pool = Resources(4, 4 * 1024**3)

        amount = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, file_type),)
        )

        assert amount == Resources(1, pool.bytes)
        assert source.stat().st_blocks * 512 < 1024**2

    def test_long_text_line_retains_whole_pool(self, tmp_path):
        file_type = "obj"
        source = tmp_path / "source"
        source.write_bytes(b"#" * (native_budget.TEXT_PREFLIGHT_LINE_BYTES + 1))
        pool = Resources(4, 4 * 1024**3)

        amount = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, file_type),)
        )

        assert amount == Resources(1, pool.bytes)

    def test_expired_text_preflight_retains_whole_pool(self, tmp_path, monkeypatch):
        file_type = "obj"
        payload = b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
        source = tmp_path / "source"
        source.write_bytes(payload)
        monkeypatch.setattr(native_budget, "TEXT_PREFLIGHT_SECONDS", -1)
        monkeypatch.setattr(native_budget, "OBJ_PREFLIGHT_WALL_SECONDS", -1)
        pool = Resources(4, 4 * 1024**3)

        amount = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, file_type),)
        )

        assert amount == Resources(1, pool.bytes)

    @pytest.mark.parametrize(
        "file_type,payload",
        [
            pytest.param("stl", ascii_stl(), id="ascii-stl"),
            pytest.param("obj", b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", id="obj"),
        ],
    )
    def test_changed_source_retains_whole_pool(
        self, tmp_path, monkeypatch, file_type, payload
    ):
        source = tmp_path / ("source." + file_type)
        source.write_bytes(payload)
        actual_stat = Path.stat
        changed = False

        def replace_before_snapshot(path, *args, **kwargs):
            nonlocal changed
            if path == source and not changed:
                changed = True
                replacement = tmp_path / "replacement"
                replacement.write_bytes(payload)
                replacement.replace(source)
            return actual_stat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "stat", replace_before_snapshot)
        pool = Resources(4, 4 * 1024**3)

        amount = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, file_type),)
        )

        assert amount == Resources(1, pool.bytes)

    def test_combines_validated_text_with_binary_complexity(self, tmp_path):
        binary = tmp_path / "large.stl"
        binary.write_bytes(binary_stl()[:80] + (200_000).to_bytes(4, "little"))
        with binary.open("r+b") as stream:
            stream.truncate(84 + 200_000 * 50)
        text = tmp_path / "text.stl"
        text.write_bytes(ascii_stl(triangles=2))
        pool = Resources(4, 4 * 1024**3)

        amount = native_budget.estimate_sources(
            pool,
            (
                native_budget.MeshSource(binary, "stl"),
                native_budget.MeshSource(text, "stl"),
            ),
        )

        assert amount == Resources(1, 200_002 * native_budget.DEFAULT_FACE_BYTES)

    def test_unknown_source_keeps_a_mixed_request_conservative(self, tmp_path):
        known = tmp_path / "known.stl"
        known.write_bytes(ascii_stl())
        unknown = tmp_path / "unknown.3mf"
        unknown.write_bytes(b"PK")
        pool = Resources(4, 4 * 1024**3)

        amount = native_budget.estimate_sources(
            pool,
            (
                native_budget.MeshSource(known, "stl"),
                native_budget.MeshSource(unknown, "3mf"),
            ),
        )

        assert amount == Resources(1, pool.bytes)

    def test_missing_source_does_not_authorize_an_optimistic_share(self, tmp_path):
        pool = Resources(4, 4 * 1024**3)

        assert native_budget.estimate_sources(
            pool, (native_budget.MeshSource(tmp_path / "missing", "stl"),)
        ) == Resources(1, pool.bytes)

    def test_large_file_preflight_does_not_scan_facets(self, tmp_path):
        source = tmp_path / "large.stl"
        source.write_bytes(
            binary_stl(triangles=1)[:80] + (20_000_000).to_bytes(4, "little")
        )
        with source.open("r+b") as stream:
            stream.truncate(84 + 20_000_000 * 50)
        pool = Resources(4, 4 * 1024**3)

        assert (
            native_budget.estimate_sources(
                pool, (native_budget.MeshSource(source, "stl"),)
            ).bytes
            == pool.bytes
        )
        assert os.stat(source).st_blocks * 512 < 1024**2

    def test_small_3mf_thumbnail_claims_only_the_raster_floor(self, tmp_path):
        source = tmp_path / "part.3mf"
        source.write_bytes(three_mf())

        amount = native_budget.estimate_sources(
            Resources(4, 4 * 1024**3),
            (native_budget.MeshSource(source, "3mf"),),
            work=THUMBNAIL_WORK,
        )

        assert amount == Resources(1, native_budget.MIN_WEBP_MEMORY)

    def test_loader_admits_every_placed_lib3mf_face_under_its_claim(self, tmp_path):
        components = [(1, f"1 0 0 0 1 0 0 0 1 {i} 0 0") for i in range(300)]
        placements = tuple((2, f"1 0 0 0 1 0 0 0 1 0 {i} 0") for i in range(300))
        source = tmp_path / "plate.3mf"
        source.write_bytes(three_mf(assemblies={2: components}, build=placements))
        placed_faces = len(tetrahedron().faces) * len(components) * len(placements)
        pool = Resources(4, 4 * 1024**3)

        amount = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "3mf"),)
        )

        assert (
            native_budget.face_capacity(amount.bytes, ".3mf", lib3mf=True)
            >= placed_faces
        )

    def test_claims_a_lib3mf_package_by_the_lib3mf_weight(self, tmp_path):
        components = [(1, f"1 0 0 0 1 0 0 0 1 {i} 0 0") for i in range(300)]
        placements = tuple((2, f"1 0 0 0 1 0 0 0 1 0 {i} 0") for i in range(300))
        source = tmp_path / "plate.3mf"
        source.write_bytes(three_mf(assemblies={2: components}, build=placements))
        placed_faces = len(tetrahedron().faces) * len(components) * len(placements)

        amount = native_budget.estimate_sources(
            Resources(4, 4 * 1024**3), (native_budget.MeshSource(source, "3mf"),)
        )

        # 360,000 placed faces: the XML weight would claim 1.3 GB of the pool.
        assert amount.bytes < placed_faces * native_budget.THREE_MF_FACE_BYTES
        assert amount.bytes >= (
            native_budget.THREE_MF_BASE_MEMORY
            + placed_faces * native_budget.THREE_MF_LIB3MF_FACE_BYTES
        )

    def test_loader_admits_every_placed_xml_face_under_its_claim(
        self, tmp_path, without_lib3mf
    ):
        components = [(1, f"1 0 0 0 1 0 0 0 1 {i} 0 0") for i in range(300)]
        placements = tuple((2, f"1 0 0 0 1 0 0 0 1 0 {i} 0") for i in range(300))
        source = tmp_path / "plate.3mf"
        source.write_bytes(three_mf(assemblies={2: components}, build=placements))
        placed_faces = len(tetrahedron().faces) * len(components) * len(placements)
        pool = Resources(4, 4 * 1024**3)

        amount = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "3mf"),)
        )

        assert amount.bytes < pool.bytes
        assert native_budget.face_capacity(amount.bytes, ".3mf") >= placed_faces

    def test_attribute_heavy_xml_package_claims_its_reached_xml(
        self, tmp_path, without_lib3mf
    ):
        with zipfile.ZipFile(io.BytesIO(three_mf())) as archive:
            model = archive.read("3D/3dmodel.model")
        painted = model.replace(b"<vertex ", b'<vertex paint="' + b"4" * 4096 + b'" ')
        source = tmp_path / "painted.3mf"
        source.write_bytes(zip_bytes({"3D/3dmodel.model": painted}))

        amount = native_budget.estimate_sources(
            Resources(4, 4 * 1024**3), (native_budget.MeshSource(source, "3mf"),)
        )

        assert (
            amount.bytes
            >= native_budget.THREE_MF_BASE_MEMORY
            + len(painted) * native_budget.THREE_MF_XML_BYTE_COST
        )

    def test_attribute_heavy_lib3mf_package_claims_its_reached_xml(self, tmp_path):
        with zipfile.ZipFile(io.BytesIO(three_mf())) as archive:
            model = archive.read("3D/3dmodel.model")
        painted = model.replace(b"<vertex ", b'<vertex paint="' + b"4" * 65536 + b'" ')
        source = tmp_path / "painted.3mf"
        source.write_bytes(zip_bytes({"3D/3dmodel.model": painted}))

        amount = native_budget.estimate_sources(
            Resources(4, 4 * 1024**3), (native_budget.MeshSource(source, "3mf"),)
        )

        assert (
            amount.bytes
            >= native_budget.THREE_MF_BASE_MEMORY
            + len(painted) * native_budget.THREE_MF_LIB3MF_XML_BYTE_COST
        )

    def test_3mf_fingerprint_analysis_keeps_the_whole_pool(self, tmp_path):
        source = tmp_path / "part.3mf"
        source.write_bytes(three_mf())
        pool = Resources(4, 4 * 1024**3)

        amount = native_budget.estimate_sources(
            pool,
            (native_budget.MeshSource(source, "3mf"),),
            work=native_budget.AnalysisWork(THUMBNAIL_WORK),
        )

        assert amount == Resources(1, pool.bytes)

    def test_3mf_beyond_the_face_cap_claims_the_whole_pool(self, tmp_path):
        components = [(1, None)] * 2048
        placements = tuple((2, None) for _ in range(300))
        source = tmp_path / "plate.3mf"
        source.write_bytes(three_mf(assemblies={2: components}, build=placements))
        pool = Resources(4, 4 * 1024**3)

        amount = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "3mf"),)
        )

        assert amount == Resources(1, pool.bytes)

    def test_records_a_counted_estimate(self, tmp_path, caplog):
        source = tmp_path / "part.3mf"
        source.write_bytes(three_mf())
        caplog.set_level(logging.INFO, logger="app.modules.media.mesh_observability")

        amount = native_budget.estimate_sources(
            Resources(4, 4 * 1024**3),
            (native_budget.MeshSource(source, "3mf"),),
            work=THUMBNAIL_WORK,
        )

        (record,) = _estimates(caplog)
        assert record["basis"] == "counted"
        assert record["file_types"] == ["3mf"]
        assert record["source_bytes"] == source.stat().st_size
        assert record["requested_bytes"] == amount.bytes

    def test_records_an_unknown_estimate(self, tmp_path, caplog):
        source = tmp_path / "broken.3mf"
        source.write_bytes(b"PK")
        caplog.set_level(logging.INFO, logger="app.modules.media.mesh_observability")
        pool = Resources(4, 4 * 1024**3)

        native_budget.estimate_sources(pool, (native_budget.MeshSource(source, "3mf"),))

        (record,) = _estimates(caplog)
        assert record["basis"] == "unknown"
        assert record["requested_bytes"] == pool.bytes

    def test_records_no_size_for_a_vanished_source(self, tmp_path, caplog):
        caplog.set_level(logging.INFO, logger="app.modules.media.mesh_observability")

        native_budget.estimate_sources(
            Resources(4, 4 * 1024**3),
            (native_budget.MeshSource(tmp_path / "missing.stl", "stl"),),
        )

        (record,) = _estimates(caplog)
        assert record["source_bytes"] is None


class TestConcurrentThreeMFAdmission:
    def test_two_small_3mf_claims_hold_permits_at_once(self, tmp_path):
        source = tmp_path / "part.3mf"
        source.write_bytes(three_mf())
        capacity = Resources(2, 2 * 1024**3)
        claim = native_budget.estimate_sources(
            capacity, (native_budget.MeshSource(source, "3mf"),), work=THUMBNAIL_WORK
        )
        pool = LocalResourcePool(tmp_path / "ledger")
        polls = 0

        def bounded_wait() -> None:
            nonlocal polls
            polls += 1
            if polls > 50:
                raise AssertionError("second 3MF claim waited for the first")

        with pool.reserve(claim, capacity, checkpoint=bounded_wait) as first:
            with pool.reserve(claim, capacity, checkpoint=bounded_wait) as second:
                assert first.resources.bytes + second.resources.bytes <= capacity.bytes


class TestObjTriangleCount:
    def test_counts_only_faces_in_obj_with_polylines(self, tmp_path):
        from app.modules.media import mesh_loading

        source = tmp_path / "mesh.obj"
        source.write_bytes(b"v 0 0 0\nv 1 0 0\nv 0 1 0\np 1\nl 1 2 3\nf 1 2 3\n")

        count = native_budget._obj_triangle_count(source, deadline=float("inf"))
        loaded = mesh_loading.load_mesh(source)

        assert loaded is not None
        assert count == len(loaded.faces) == 1

    @pytest.mark.parametrize(
        "face,expected",
        [
            pytest.param(b"f 1 2 3", 1, id="triangle"),
            pytest.param(b"f -3 -2 -1", 1, id="relative-indices"),
            pytest.param(b"f 1 2 3 1", 2, id="quad"),
            pytest.param(b"f 1 2 3 1 2", 3, id="ngon"),
        ],
    )
    def test_counts_triangulated_faces(self, tmp_path, face, expected):
        source = tmp_path / "mesh.obj"
        source.write_bytes(
            b"# mesh\n\no part\ng group\ns off\nv 0 0 0\nv 1 0 0\nv 0 1 0\n"
            + face
            + b"\n"
        )

        count = native_budget._obj_triangle_count(source, deadline=float("inf"))

        assert count == expected


class TestAdmittedGeometry:
    @pytest.mark.parametrize(
        "file_type,payload",
        [
            pytest.param("stl", ascii_stl(), id="ascii-stl"),
            pytest.param(
                "obj",
                b"v 0 0 0\nv 1 0 0\nv 0 1 0\nvn 0 0 1\nf 1//1 2//1 3//1\n",
                id="obj",
            ),
        ],
    )
    def test_text_source_renders_under_its_weighted_native_ceiling(
        self, tmp_path, monkeypatch, file_type, payload
    ):
        from app.core.config import _overlay
        from app.modules.media import mesh_isolation
        from app.modules.media.mesh_contracts import ThumbnailRequest

        source = tmp_path / ("source." + file_type)
        source.write_bytes(payload)
        monkeypatch.setitem(_overlay, "compute_mode", "cpu")
        pool = Resources(4, 4 * 1024**3)
        amount = native_budget.estimate_sources(
            pool,
            (native_budget.MeshSource(source, file_type),),
            work=native_budget.RasterWork(96, 72, 1, native_budget.RasterCodec.PNG),
        )

        with admit(amount, pool, checkpoint=lambda: None):
            result = mesh_isolation.generate(
                ThumbnailRequest(source, width=96, height=72, include_fingerprint=False)
            )

        assert amount == Resources(1, 512 * 1024**2)
        assert result.image is not None
        assert result.geometry["triangle_count"] == 1

    def test_loader_uses_its_weighted_allowance(self, monkeypatch):
        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "mesh_memory_budget_fraction", 0.5)
        amount = Resources(1, 512 * 1024**2)
        with admit(amount, Resources(4, 4 * 1024**3), checkpoint=lambda: None):
            cap = mesh_policy.ram_triangle_cap(".stl")

        assert cap == (512 * 1024**2) // 3000


class _CapturedWork(Exception):
    pass


class TestCallerProfiles:
    @pytest.mark.parametrize(
        "include_thumbnail,include_fingerprint,output_format,width,height,expected",
        [
            pytest.param(
                False, False, "PNG", None, None, "geometry", id="geometry-only"
            ),
            pytest.param(True, False, "PNG", 32, 24, "png", id="small-png"),
            pytest.param(True, False, "WEBP", None, None, "webp", id="configured-webp"),
            pytest.param(
                False, True, "PNG", None, None, "analysis", id="analysis-only"
            ),
            pytest.param(
                True, True, "WEBP", 320, 240, "analysis-webp", id="analysis-with-webp"
            ),
        ],
    )
    def test_mesh_request_forwards_its_actual_work(
        self,
        tmp_path,
        monkeypatch,
        include_thumbnail,
        include_fingerprint,
        output_format,
        width,
        height,
        expected,
    ):
        from app.core.config import _overlay
        from app.modules.media import mesh_isolation
        from app.modules.media.mesh_contracts import ThumbnailRequest

        monkeypatch.setitem(_overlay, "model_thumbnail_width", 640)
        path = tmp_path / "mesh.stl"
        path.write_bytes(binary_stl(triangles=12))
        captured = []

        def observe(
            module,
            spec,
            *,
            sources,
            work,
            on_chunk=None,
            amount=None,
        ):
            # Other formats keep the estimate: no claim is imposed on it.
            assert amount is None
            captured.append((sources, work))
            raise _CapturedWork

        monkeypatch.setattr(mesh_isolation, "_run_worker", observe)
        request = ThumbnailRequest(
            path,
            include_thumbnail=include_thumbnail,
            include_fingerprint=include_fingerprint,
            output_format=output_format,
            width=width,
            height=height,
        )
        with pytest.raises(_CapturedWork):
            mesh_isolation.generate(request)
        ((sources, work),) = captured
        assert sources == (native_budget.MeshSource(path, ".stl"),)
        if expected == "geometry":
            assert work == native_budget.GeometryWork()
        elif expected == "analysis":
            assert work == native_budget.AnalysisWork()
        else:
            raster = native_budget.RasterWork(
                width or 640,
                height or 480,
                1,
                native_budget.RasterCodec.WEBP
                if "webp" in expected
                else native_budget.RasterCodec.PNG,
            )
            assert work == (
                native_budget.AnalysisWork(raster)
                if expected == "analysis-webp"
                else raster
            )

    @pytest.mark.parametrize("consumer", ["embedding", "verification", "conversion"])
    def test_other_native_callers_forward_explicit_work(
        self, tmp_path, monkeypatch, consumer
    ):
        from app.modules.media import (
            embedding_isolation,
            mesh_isolation,
            stl_isolation,
            verification_isolation,
        )

        source = tmp_path / "mesh.3mf"
        source.write_bytes(b"PK")
        captured = []

        def observe(module, spec, *, sources, work, workspace=None, reply_limit=None):
            captured.append(work)
            if consumer == "conversion":
                assert workspace == tmp_path
                assert reply_limit == 44
                assert sources == (native_budget.MeshSource(source, "3mf"),)
                assert (
                    spec["expected_sha256"]
                    == hashlib.sha256(source.read_bytes()).hexdigest()
                )
            raise _CapturedWork

        monkeypatch.setattr(mesh_isolation, "run_worker", observe)
        monkeypatch.setattr(mesh_isolation, "prepared_worker_result", observe)
        with pytest.raises(_CapturedWork):
            if consumer == "embedding":
                embedding_isolation.embedding_views(
                    source,
                    file_type="3mf",
                    component_index=0,
                    image_size=128,
                    triangle_cap=1000,
                )
            elif consumer == "verification":
                verification_isolation.verify_paths(
                    source, source, first_type="3mf", second_type="3mf"
                )
            else:
                with stl_isolation.prepare_stl(
                    source,
                    file_type="3mf",
                    expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                    workspace=tmp_path,
                ):
                    pytest.fail("conversion bypassed native supervision")
        (work,) = captured
        if consumer == "embedding":
            assert work == native_budget.RasterWork(
                128, 128, 6, native_budget.RasterCodec.RGB
            )
        elif consumer == "verification":
            assert work == native_budget.AnalysisWork()
        else:
            assert work == native_budget.GeometryWork()

    def test_source_estimation_retains_the_requested_profile(self, tmp_path):
        source = tmp_path / "cube.stl"
        source.write_bytes(binary_stl(triangles=12))
        pool = Resources(4, 4 * 1024**3)
        work = native_budget.AnalysisWork()
        assert native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "stl"),), work=work
        ) == Resources(1, 1024**3)

    def test_nested_work_retains_the_existing_hard_claim(self, tmp_path, monkeypatch):
        from app.modules.media import mesh_isolation, native_process
        from app.runtime.native_runtime import current_permit

        source = tmp_path / "cube.stl"
        source.write_bytes(binary_stl(triangles=12))
        pool = Resources(2, 2 * 1024**3)
        amount = Resources(1, 512 * 1024**2)
        monkeypatch.setattr(native_process, "native_capacity", lambda: pool)
        observed = []

        def no_reestimate(*args, **kwargs):
            pytest.fail("nested work requested a second allowance")

        def command(module, arguments, ceiling):
            observed.append(ceiling)
            return ["probe"]

        def supervise(
            command, *, memory_budget, timeout_seconds, permit, lifecycle, on_chunk
        ):
            assert current_permit() is permit
            assert memory_budget == amount.bytes
            assert permit.resources == amount
            raise _CapturedWork

        monkeypatch.setattr(mesh_isolation, "estimate_sources", no_reestimate)
        monkeypatch.setattr(mesh_isolation, "worker_command", command)
        monkeypatch.setattr(mesh_isolation, "supervise_result", supervise)
        with admit(amount, pool, checkpoint=lambda: None):
            with pytest.raises(_CapturedWork):
                mesh_isolation._run_worker(
                    "app.modules.media.mesh_worker",
                    {},
                    sources=(native_budget.MeshSource(source, "stl"),),
                    work=native_budget.AnalysisWork(),
                )
        assert observed == [amount.bytes]

    def test_streaming_preview_accounts_for_larger_framebuffer(
        self, tmp_path, monkeypatch
    ):
        from app.modules.media import mesh_isolation, native_process, stl_streaming
        from app.runtime.native_runtime import current_permit

        source = tmp_path / "cube.stl"
        source.write_bytes(binary_stl(triangles=12))
        monkeypatch.setattr(
            native_process, "native_capacity", lambda: Resources(2, 2 * 1024**3)
        )
        from app.modules.media import worker_bootstrap

        observed = []
        bootstrap_ceilings = []
        original_command = worker_bootstrap.command

        def command(module, arguments, ceiling):
            bootstrap_ceilings.append(ceiling)
            return original_command(module, arguments, ceiling)

        def observe(command, *, memory_budget, permit, **kwargs):
            assert current_permit() is permit
            observed.append((permit.resources.bytes, memory_budget))
            raise _CapturedWork

        monkeypatch.setattr(worker_bootstrap, "command", command)
        monkeypatch.setattr(mesh_isolation, "supervise_result", observe)
        with pytest.raises(_CapturedWork):
            stl_streaming.render_stl_preview_isolated(source, width=1280, height=960)
        # The work claim and pre-import AS ceiling include the framebuffer.
        # Streaming retains its independently stricter 256 MiB RSS limit.
        expected = 512 * 1024**2 + 58_982_400
        assert observed == [(expected, 256 * 1024**2)]
        assert bootstrap_ceilings == [expected]
        assert current_permit() is None
        assert source.read_bytes() == binary_stl(triangles=12)


class TestAsciiTriangleBound:
    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(ascii_stl(triangles=3), id="ordinary"),
            pytest.param(ascii_stl(triangles=3).rstrip(), id="no-final-newline"),
            pytest.param(ascii_stl(triangles=3).replace(b"\n", b"\r\n"), id="crlf"),
            pytest.param(
                ascii_stl(triangles=3).replace(b"endfacet", b"# comment\n\nendfacet"),
                id="comments",
            ),
            pytest.param(
                ascii_stl(triangles=3) + b"# pad\n" * 90000, id="multiple-read-blocks"
            ),
        ],
    )
    def test_bounds_authoritative_triangle_count(self, tmp_path, payload):
        from app.modules.media.stl_reader import scan_stl

        source = tmp_path / "source.stl"
        source.write_bytes(payload)
        actual = scan_stl(source).triangle_count

        bound = native_budget._ascii_triangle_bound(source)

        assert bound is not None
        assert bound >= actual == 3

    @pytest.mark.parametrize(
        "prefix",
        [b"1902\n", b".793998\n", b"cet normal 0 0 1\n", b"ex 0 0 0\n"],
        ids=[
            "truncated-number",
            "truncated-coordinate",
            "truncated-facet",
            "truncated-vertex",
        ],
    )
    def test_ambiguous_prefix_keeps_loader_density_headroom(self, tmp_path, prefix):
        source = tmp_path / "source.stl"
        source.write_bytes(prefix + ascii_stl(triangles=3))
        pool = Resources(4, 4 * 1024**3)

        claim = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "stl"),)
        )

        assert claim == Resources(1, native_budget.MIN_NATIVE_MEMORY)
        assert native_budget._ascii_triangle_bound(source) is None
        assert native_budget.face_capacity(
            claim.bytes, ".stl"
        ) >= mesh_policy.estimate_triangle_count(source)

    def test_cpu_watchdog_declines_detailed_count(self, tmp_path, monkeypatch):
        source = tmp_path / "source.stl"
        source.write_bytes(ascii_stl())
        clock = iter([0.0, 0.251])
        monkeypatch.setattr(native_budget.time, "thread_time", lambda: next(clock))

        bound = native_budget._ascii_triangle_bound(source)

        assert bound is None

    def test_canonical_worker_rejects_invalid_tail(self, tmp_path, monkeypatch):
        from app.core.config import _overlay
        from app.modules.media import mesh_isolation
        from app.modules.media.mesh_contracts import (
            ThumbnailFailureReason,
            ThumbnailRequest,
        )

        source = tmp_path / "source.stl"
        source.write_bytes(ascii_stl(triangles=2) + b"facet normal 0 0 1\n")
        monkeypatch.setitem(_overlay, "compute_mode", "cpu")
        pool = Resources(4, 4 * 1024**3)
        claim = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "stl"),)
        )

        with admit(claim, pool, checkpoint=lambda: None):
            result = mesh_isolation.generate(
                ThumbnailRequest(source, width=96, height=72)
            )

        assert claim == Resources(1, native_budget.MIN_NATIVE_MEMORY)
        assert result.image is None
        assert result.failure_reason is ThumbnailFailureReason.INVALID_SOURCE


class TestStableStlByteBound:
    @pytest.mark.parametrize(
        "payload",
        [
            b"solid text\nendsolid\n",
            b"truncated",
            b"",
            b"#" * (native_budget.TEXT_PREFLIGHT_LINE_BYTES + 1),
            ascii_stl().replace(b"vertex 1 0 0", b"vertex nan 0 0"),
        ],
    )
    def test_small_stl_does_not_monopolize_the_pool(self, tmp_path, payload):
        source = tmp_path / "source.stl"
        source.write_bytes(payload)
        pool = Resources(4, 4 * 1024**3)
        claim = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "stl"),)
        )
        assert claim == Resources(1, native_budget.MIN_NATIVE_MEMORY)

    def test_expired_scan_keeps_a_source_byte_bound(self, tmp_path, monkeypatch):
        source = tmp_path / "source.stl"
        source.write_bytes(ascii_stl())
        monkeypatch.setattr(native_budget, "OBJ_PREFLIGHT_WALL_SECONDS", -1)
        pool = Resources(4, 4 * 1024**3)
        claim = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "stl"),)
        )
        assert claim == Resources(1, native_budget.MIN_NATIVE_MEMORY)

    def test_large_malformed_stl_retains_loader_headroom(self, tmp_path):
        source = tmp_path / "source.stl"
        with source.open("wb") as stream:
            stream.write(b"not an STL")
            stream.truncate(16 * 1024**2)
        pool = Resources(4, 4 * 1024**3)
        claim = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "stl"),)
        )
        assert native_budget.MIN_NATIVE_MEMORY < claim.bytes < pool.bytes
        assert native_budget.face_capacity(
            claim.bytes, ".stl"
        ) >= mesh_policy.estimate_triangle_count(source)
        assert source.stat().st_blocks * 512 < 1024**2

    @pytest.mark.parametrize(
        "prefix", [b"1902\n", b".793998\n", b"cet normal 0 0 1\n", b"ex 0 0 0\n"]
    )
    def test_invalid_prefix_stays_invalid_under_weighted_admission(
        self, tmp_path, monkeypatch, prefix
    ):
        from app.core.config import _overlay
        from app.modules.media import mesh_isolation
        from app.modules.media.mesh_contracts import (
            ThumbnailFailureReason,
            ThumbnailRequest,
        )

        source = tmp_path / "source.stl"
        source.write_bytes(prefix + ascii_stl() + b" " * (12 * 1024**2))
        monkeypatch.setitem(_overlay, "compute_mode", "cpu")
        pool = Resources(4, 4 * 1024**3)
        claim = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "stl"),)
        )
        with admit(claim, pool, checkpoint=lambda: None):
            result = mesh_isolation.generate(
                ThumbnailRequest(source, width=96, height=72)
            )
        assert claim.bytes < pool.bytes
        assert result.image is None
        assert result.failure_reason is ThumbnailFailureReason.INVALID_SOURCE


class TestWeightedObjRefusals:
    @pytest.mark.parametrize(
        "face",
        [
            b"f 1 2 4",
            b"f -4 -2 -1",
            b"f 0 2 3",
            b"f 1//2 2//2 3//2",
            b"f 1///1 2///1 3///1",
            b"f 1// 2// 3//",
        ],
    )
    def test_weighted_admission_preserves_guarded_loader_outcome(
        self, tmp_path, monkeypatch, face
    ):
        from app.core.config import _overlay
        from app.modules.media import mesh_isolation
        from app.modules.media.mesh_contracts import ThumbnailRequest

        source = tmp_path / "source.obj"
        source.write_bytes(b"v 0 0 0\nv 1 0 0\nv 0 1 0\nvn 0 0 1\n" + face + b"\n")
        monkeypatch.setitem(_overlay, "compute_mode", "cpu")
        pool = Resources(4, 4 * 1024**3)
        claim = native_budget.estimate_sources(
            pool, (native_budget.MeshSource(source, "obj"),)
        )
        request = ThumbnailRequest(source, include_thumbnail=False)
        with admit(Resources(1, pool.bytes), pool, checkpoint=lambda: None):
            reference = mesh_isolation.generate(request)
        with admit(claim, pool, checkpoint=lambda: None):
            actual = mesh_isolation.generate(request)
        assert claim.bytes < pool.bytes
        assert actual.geometry == reference.geometry
        assert actual.geometry_outcome == reference.geometry_outcome
        assert actual.volume == reference.volume
        assert actual.failure_reason == reference.failure_reason
        assert actual.image is None
