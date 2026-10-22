"""3MF structure bounds never fall below what the guarded reader will hold.

Native admission sizes a worker's hard memory ceiling from these bounds, so an
undercount would turn a package that reads today into a resource failure. Every
package the scanner cannot bound with certainty must report ``None`` and keep
the whole-pool claim.
"""

import io
import zipfile

import pytest

from app.modules.media import three_mf_bounds, three_mf_package
from app.modules.media.three_mf_bounds import ThreeMFBounds, bounds
from tests.factories.content import zip_bytes
from tests.factories.geometry import tetrahedron, three_mf
from tests.factories.three_mf_pilot import opc_package

PRODUCTION = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
MATERIALS = "http://schemas.microsoft.com/3dmanufacturing/material/2015/02"
FACE_CAP = 2_000_000
PART_LINK = (
    b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    b'<Relationship Id="part" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"'
    b' Target="/3D/Objects/part.model"/></Relationships>'
)


def _bounds(path, **overrides):
    options = {
        "face_cap": FACE_CAP,
        "max_model_bytes": 256 * 1024 * 1024,
        "wall_seconds": 60.0,
        "cpu_seconds": 60.0,
        **overrides,
    }
    return bounds(path, **options)


def _write(tmp_path, payload: bytes):
    path = tmp_path / "package.3mf"
    path.write_bytes(payload)
    return path


def _root(payload: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return archive.read("3D/3dmodel.model")


def _with_external_part(part: bytes, *, linked: bool = True) -> bytes:
    """A root that places object 7 from ``3D/Objects/part.model``.

    Producers link every part from the root's relationships; ``linked=False``
    leaves only the production path to it.
    """
    extras = {"3D/Objects/part.model": part}
    if linked:
        extras["3D/_rels/3dmodel.model.rels"] = PART_LINK
    return three_mf(
        meshes={},
        assemblies={2: [(7, None)]},
        build=((2, None),),
        external_paths={7: "/3D/Objects/part.model"},
        extras=extras,
    )


class TestBounds:
    def test_counts_every_triangle_of_a_placed_object(self, tmp_path):
        result = _bounds(_write(tmp_path, three_mf()))

        assert result is not None
        assert result.placed_faces >= len(tetrahedron().faces)

    def test_counts_a_component_once_per_placement(self, tmp_path):
        single = _bounds(_write(tmp_path, three_mf()))
        payload = three_mf(
            assemblies={2: [(1, None), (1, "1 0 0 0 1 0 0 0 1 20 0 0")]},
            build=((2, None), (2, "1 0 0 0 1 0 0 0 1 0 40 0")),
        )

        repeated = _bounds(_write(tmp_path, payload))

        assert single is not None
        assert repeated == ThreeMFBounds(
            repeated.reached_model_bytes, 4 * single.placed_faces, lib3mf_readable=True
        )

    def test_follows_a_production_path_into_another_part(self, tmp_path):
        external = three_mf(meshes={7: tetrahedron()}, build=())
        payload = three_mf(
            meshes={},
            assemblies={2: [(7, None)]},
            build=((2, None),),
            external_paths={7: "/3D/Objects/part.model"},
            extras={"3D/Objects/part.model": _root(external)},
        )

        result = _bounds(_write(tmp_path, payload))

        assert result is not None
        assert result.placed_faces >= len(tetrahedron().faces)
        assert result.reached_model_bytes == len(_root(payload)) + len(_root(external))

    def test_ignores_a_path_attribute_outside_the_production_namespace(self, tmp_path):
        payload = (
            _root(three_mf())
            .replace(b"<model ", b'<model xmlns:x="urn:other" ', 1)
            .replace(b"<item ", b'<item x:path="/3D/missing.model" ', 1)
        )

        result = _bounds(
            _write(tmp_path, opc_package(zip_bytes({"3D/3dmodel.model": payload})))
        )

        assert result is not None
        assert result.placed_faces >= len(tetrahedron().faces)

    def test_excludes_unreached_parts_from_reached_bytes(self, tmp_path):
        unreached = three_mf(meshes={9: tetrahedron()}, build=((9, None),))
        payload = three_mf(extras={"3D/Objects/unused.model": _root(unreached)})

        result = _bounds(_write(tmp_path, payload))

        assert result is not None
        assert result.reached_model_bytes == len(_root(payload))

    @pytest.mark.parametrize(
        "chunk", [1, 7, 13, 64, 257], ids=lambda size: f"window-{size}"
    )
    def test_counts_tags_split_across_read_windows(self, tmp_path, monkeypatch, chunk):
        payload = three_mf(
            assemblies={2: [(1, None), (1, None)]}, build=((2, None), (1, None))
        )
        whole = _bounds(_write(tmp_path, payload))
        monkeypatch.setattr(three_mf_package, "_CHUNK_BYTES", chunk)
        windowed = tmp_path / "windowed"
        windowed.mkdir()

        assert _bounds(_write(windowed, payload)) == whole

    def test_caps_placed_faces_one_above_the_face_cap(self, tmp_path):
        placements = tuple((1, None) for _ in range(50))

        result = _bounds(_write(tmp_path, three_mf(build=placements)), face_cap=10)

        assert result is not None
        assert result.placed_faces == 11

    def test_reads_the_root_named_by_the_package_relationship(self, tmp_path):
        payload = three_mf(
            model_part="3D/root.model", relationship_target="/3D/root.model"
        )

        result = _bounds(_write(tmp_path, payload))

        assert result is not None
        assert result.placed_faces >= len(tetrahedron().faces)

    def test_marks_a_plain_package_lib3mf_readable(self, tmp_path):
        result = _bounds(_write(tmp_path, three_mf()))

        assert result is not None
        assert result.lib3mf_readable

    def test_marks_a_production_part_with_its_own_build_lib3mf_readable(self, tmp_path):
        part = _root(three_mf(meshes={7: tetrahedron()}, build=()))

        result = _bounds(_write(tmp_path, _with_external_part(part)))

        assert result is not None
        assert result.lib3mf_readable

    def test_leaves_a_production_part_without_a_build_to_the_xml_reader(self, tmp_path):
        # lib3mf refuses such a part, so the worker reads the package as XML
        # and the claim has to cover that reader.
        part = _root(three_mf(meshes={7: tetrahedron()}, build=())).replace(
            b"<build />", b""
        )

        result = _bounds(_write(tmp_path, _with_external_part(part)))

        assert result is not None
        assert not result.lib3mf_readable

    def test_leaves_a_package_without_opc_entries_to_the_xml_reader(self, tmp_path):
        result = _bounds(_write(tmp_path, three_mf(opc=False)))

        assert result is not None
        assert not result.lib3mf_readable

    def test_leaves_a_required_extension_to_the_xml_reader(self, tmp_path):
        payload = _root(three_mf()).replace(
            b"<model ",
            f'<model xmlns:m="{MATERIALS}" requiredextensions="m" '.encode(),
            1,
        )

        result = _bounds(
            _write(tmp_path, opc_package(zip_bytes({"3D/3dmodel.model": payload})))
        )

        assert result is not None
        assert not result.lib3mf_readable

    def test_leaves_a_part_the_root_does_not_link_to_the_xml_reader(self, tmp_path):
        part = _root(three_mf(meshes={7: tetrahedron()}, build=()))

        result = _bounds(_write(tmp_path, _with_external_part(part, linked=False)))

        assert result is not None
        assert not result.lib3mf_readable

    def test_leaves_parts_in_different_units_to_the_xml_reader(self, tmp_path):
        part = _root(three_mf(meshes={7: tetrahedron()}, build=(), unit="inch"))

        result = _bounds(_write(tmp_path, _with_external_part(part)))

        assert result is not None
        assert not result.lib3mf_readable

    def test_leaves_a_document_type_declaration_to_the_xml_reader(self, tmp_path):
        payload = b"<!DOCTYPE model>" + _root(three_mf())

        result = _bounds(
            _write(tmp_path, opc_package(zip_bytes({"3D/3dmodel.model": payload})))
        )

        assert result is not None
        assert not result.lib3mf_readable

    def test_leaves_every_package_to_the_xml_reader_without_lib3mf(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(three_mf_bounds, "lib3mf_available", lambda: False)

        result = _bounds(_write(tmp_path, three_mf()))

        assert result is not None
        assert not result.lib3mf_readable

    def test_remembers_the_bounds_of_an_unchanged_file(self, tmp_path, monkeypatch):
        path = _write(tmp_path, three_mf())
        first = _bounds(path)

        def unread(*_args, **_kwargs):
            raise AssertionError("an unchanged package was scanned twice")

        monkeypatch.setattr(three_mf_package, "windows", unread)

        assert _bounds(path) == first

    def test_measures_a_changed_file_again(self, tmp_path):
        path = _write(tmp_path, three_mf())
        single = _bounds(path)
        path.write_bytes(three_mf(build=((1, None), (1, None))))

        repeated = _bounds(path)

        assert single is not None
        assert repeated is not None
        assert repeated.placed_faces == 2 * single.placed_faces

    def test_refuses_a_file_that_is_not_an_archive(self, tmp_path):
        assert _bounds(_write(tmp_path, b"PK not really")) is None

    def test_refuses_a_cyclic_component_graph(self, tmp_path):
        payload = three_mf(
            meshes={}, assemblies={2: [(3, None)], 3: [(2, None)]}, build=((2, None),)
        )

        assert _bounds(_write(tmp_path, payload)) is None

    def test_refuses_a_reference_to_a_missing_object(self, tmp_path):
        assert _bounds(_write(tmp_path, three_mf(build=((5, None),)))) is None

    def test_refuses_duplicate_object_ids(self, tmp_path):
        payload = _root(three_mf(meshes={1: tetrahedron(), 2: tetrahedron()})).replace(
            b'id="2"', b'id="1"'
        )

        assert (
            _bounds(
                _write(tmp_path, opc_package(zip_bytes({"3D/3dmodel.model": payload})))
            )
            is None
        )

    def test_refuses_a_path_that_escapes_the_package(self, tmp_path):
        payload = three_mf(
            meshes={},
            assemblies={2: [(7, None)]},
            build=((2, None),),
            external_paths={7: "/3D/../../part.model"},
        )

        assert _bounds(_write(tmp_path, payload)) is None

    def test_refuses_a_namespace_declared_below_the_model(self, tmp_path):
        payload = _root(three_mf()).replace(
            b"<item ", f'<item xmlns:p="{PRODUCTION}" '.encode(), 1
        )

        assert (
            _bounds(
                _write(tmp_path, opc_package(zip_bytes({"3D/3dmodel.model": payload})))
            )
            is None
        )

    def test_refuses_triangles_outside_any_object(self, tmp_path):
        payload = _root(three_mf()).replace(
            b"<resources>", b"<resources><!-- triangle -->", 1
        )

        assert (
            _bounds(
                _write(tmp_path, opc_package(zip_bytes({"3D/3dmodel.model": payload})))
            )
            is None
        )

    def test_refuses_two_root_relationships(self, tmp_path):
        relationship = (
            b'<Relationship Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"'
            b' Target="/3D/3dmodel.model" Id="r%d"/>'
        )
        rels = (
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + relationship % 1
            + relationship % 2
            + b"</Relationships>"
        )
        payload = zip_bytes(
            {"3D/3dmodel.model": _root(three_mf()), "_rels/.rels": rels}
        )

        assert _bounds(_write(tmp_path, payload)) is None

    def test_refuses_an_unterminated_tag_beyond_the_carry(self, tmp_path, monkeypatch):
        monkeypatch.setattr(three_mf_package, "_CHUNK_BYTES", 16)
        monkeypatch.setattr(three_mf_package, "_MAX_CARRY_BYTES", 32)
        payload = _root(three_mf()).replace(
            b"<item ", b'<item note="' + b"x" * 64 + b'" ', 1
        )

        assert (
            _bounds(
                _write(tmp_path, opc_package(zip_bytes({"3D/3dmodel.model": payload})))
            )
            is None
        )

    def test_refuses_model_bytes_over_the_reader_budget(self, tmp_path):
        payload = three_mf()

        assert _bounds(_write(tmp_path, payload), max_model_bytes=16) is None

    def test_refuses_a_part_compressed_beyond_the_reader_ratio(self, tmp_path):
        payload = _root(three_mf()).replace(
            b"<resources>", b"<resources>" + b" " * (4 * 1024 * 1024), 1
        )
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
            package.writestr("3D/3dmodel.model", payload)

        assert _bounds(_write(tmp_path, archive.getvalue())) is None

    def test_refuses_more_entries_than_the_reader_accepts(self, tmp_path):
        entries = {f"Metadata/{index}.txt": b"" for index in range(4096)}
        payload = zip_bytes({"3D/3dmodel.model": _root(three_mf()), **entries})

        assert _bounds(_write(tmp_path, payload)) is None

    def test_stops_when_the_cpu_budget_runs_out(self, tmp_path, monkeypatch):
        clock = iter([0.0, 10.0])
        monkeypatch.setattr(
            three_mf_bounds.time, "thread_time", lambda: next(clock, 10.0)
        )

        assert _bounds(_write(tmp_path, three_mf()), cpu_seconds=1.0) is None

    def test_stops_when_the_wall_budget_runs_out(self, tmp_path, monkeypatch):
        clock = iter([0.0, 10.0])
        monkeypatch.setattr(
            three_mf_bounds.time, "monotonic", lambda: next(clock, 10.0)
        )

        assert _bounds(_write(tmp_path, three_mf()), wall_seconds=1.0) is None
