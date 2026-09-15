"""Streaming XML extraction must preserve complete geometry without a DOM."""

import io
import xml.etree.ElementTree as ET

import numpy as np
import printstash_mesh_native as native
import pytest

XML = b"""<model unit="millimeter"><resources><object id="1"><mesh><vertices>
<vertex x="1.25" y="-2" z="3e-2"/><vertex x="4" y="5" z="6"/>
<vertex x="7" y="8" z="9"/></vertices><triangles>
<triangle v1="2" v2="0" v3="1"/></triangles></mesh></object></resources>
<build><item objectid="1"/></build></model>"""


class TestParse3mfXml:
    def test_retains_coordinates(self):
        _, meshes = native.parse_3mf_xml(io.BytesIO(XML))
        np.testing.assert_array_equal(
            np.frombuffer(meshes[0][0], dtype=np.float64).reshape(-1, 3),
            [[1.25, -2, 0.03], [4, 5, 6], [7, 8, 9]],
        )

    def test_retains_face_order(self):
        _, meshes = native.parse_3mf_xml(io.BytesIO(XML))
        np.testing.assert_array_equal(
            np.frombuffer(meshes[0][1], dtype=np.int64), [2, 0, 1]
        )

    def test_removes_geometry_from_metadata_xml(self):
        shell, _ = native.parse_3mf_xml(io.BytesIO(XML))
        root = ET.fromstring(shell)
        assert root.find("./resources/object/mesh") is not None
        assert root.find(".//vertex") is None
        assert root.find("./build/item").attrib == {"objectid": "1"}

    def test_handles_short_reads(self):
        class ShortReader(io.BytesIO):
            def read(self, size=-1):
                assert 0 < size <= 65536
                return super().read(min(size, 7))

        actual = native.parse_3mf_xml(ShortReader(XML))
        assert actual == native.parse_3mf_xml(io.BytesIO(XML))

    def test_keeps_multiple_meshes_separate(self):
        mesh = XML[XML.index(b"<mesh>") : XML.index(b"</mesh>") + 7]
        source = b"<model><object>" + mesh + mesh + b"</object></model>"
        _, meshes = native.parse_3mf_xml(io.BytesIO(source))
        assert len(meshes) == 2
        assert meshes[0] == meshes[1]

    @pytest.mark.parametrize(
        "source,match",
        [
            (XML[:-8], "XML"),
            (XML.replace(b'x="1.25"', b'x="NaN"'), "finite"),
            (XML.replace(b'x="1.25"', b'x="wrong"'), "coordinate"),
            (XML.replace(b'x="1.25"', b'q="1.25"'), "attribute"),
            (XML.replace(b'v1="2"', b'v1="3"'), "index"),
            (XML.replace(b'v1="2"', b'v1="-1"'), "index"),
            (b'<!DOCTYPE model [<!ENTITY x "oops">]>' + XML, "DTD"),
        ],
    )
    def test_rejects_invalid_geometry(self, source, match):
        with pytest.raises(ValueError, match=match):
            native.parse_3mf_xml(io.BytesIO(source))

    def test_enforces_input_limit(self):
        with pytest.raises(ValueError, match="limit"):
            native.parse_3mf_xml(io.BytesIO(XML), max_bytes=100)

    def test_propagates_read_failure(self):
        class BrokenReader:
            def read(self, size):
                raise OSError("read failed")

        with pytest.raises(ValueError, match="read failed"):
            native.parse_3mf_xml(BrokenReader())

    @pytest.mark.parametrize(
        "xml,match",
        [
            (b"<model><mesh><mesh/></mesh></model>", "nested XML mesh"),
            (b"<a>" * 257 + b"</a>" * 257, "nesting limit"),
            (b"<model>" + b"x" * (16 * 1024**2) + b"</model>", "metadata limit"),
            (XML.replace(b'x="1.25"', b'x="&unknown;"'), "unrecognized entity"),
            (XML.replace(b'x="1.25"', b'x="1" x="2"'), "duplicated"),
            (XML.replace(b'v1="2"', b'v1="&unknown;"'), "unrecognized entity"),
            (XML.replace(b'v1="2"', b'v1="1" v1="2"'), "duplicated"),
            (b"<model></wrong>", "invalid XML"),
        ],
    )
    def test_refuses_invalid_xml_structure(self, xml, match):
        with pytest.raises(ValueError, match=match):
            native.parse_3mf_xml(io.BytesIO(xml))

    def test_retains_empty_mesh(self):
        _, meshes = native.parse_3mf_xml(io.BytesIO(b"<model><mesh/></model>"))
        assert meshes == [(b"", b"")]

    def test_accepts_explicit_vertex_end_tags(self):
        expanded = XML.replace(
            b'<vertex x="1.25" y="-2" z="3e-2"/>',
            b'<vertex x="1.25" y="-2" z="3e-2"></vertex>',
        )
        assert native.parse_3mf_xml(io.BytesIO(expanded)) == native.parse_3mf_xml(
            io.BytesIO(XML)
        )

    def test_requires_byte_reads(self):
        with pytest.raises(ValueError, match="bytes"):
            native.parse_3mf_xml(io.StringIO(XML.decode()))

    def test_refuses_reader_exceeding_request(self):
        class OversizedReader:
            def read(self, size):
                return b" " * (size + 1)

        with pytest.raises(ValueError, match="input limit"):
            native.parse_3mf_xml(OversizedReader())
