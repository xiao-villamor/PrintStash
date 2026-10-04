"""Deterministic small 3MF source variants; these are synthetic, not slicer exports."""

from __future__ import annotations

import io
import struct
import zipfile
import zlib
from enum import StrEnum
from xml.sax.saxutils import escape

from scripts.mesh_corpus_v2_geometry import Mesh, box, permute

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
PRODUCTION = "http://schemas.microsoft.com/3dmanufacturing/production/2015/06"
ROOT_TYPE = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
UNIT_SCALE = {
    "micron": 0.001,
    "millimeter": 1.0,
    "centimeter": 10.0,
    "inch": 25.4,
    "foot": 304.8,
    "meter": 1000.0,
}


def _png() -> bytes:
    def chunk(kind: bytes, content: bytes) -> bytes:
        return (
            struct.pack(">I", len(content))
            + kind
            + content
            + struct.pack(">I", zlib.crc32(kind + content))
        )

    pixels = (b"\x00" + b"\xff\xff\xff" * 256) * 192
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 256, 192, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(pixels, level=9))
        + chunk(b"IEND", b"")
    )


PNG = _png()


class SceneCase(StrEnum):
    STANDARD = "standard"
    ROOT_RELATIONSHIP = "root_relationship"
    EMPTY = "empty_geometry"
    INVALID = "invalid_index"
    DUPLICATE_ID = "duplicate_id"
    UNREFERENCED = "unreferenced_parts"
    PRINTABLE = "printable"
    INTERNAL_PART = "internal_part"
    NESTED = "nested_transforms"
    CYCLE = "cycle"
    REFLECTION = "reflection"
    SINGULAR = "singular_transform"
    EXPONENTIAL = "exponential_expansion"
    UNKNOWN_REQUIRED = "unknown_required_extension"
    PREVIEW_VALID = "preview_valid"
    PREVIEW_BROKEN = "preview_broken"
    PREVIEW_ABSENT = "preview_absent"
    PLATES = "multiple_plates"
    FOREIGN_METADATA = "foreign_metadata"
    AUXILIARY = "auxiliary_object"
    SOURCE_TRANSLATION = "source_translation"
    LARGE_SCALE = "large_scale"
    PERMUTED = "permuted"
    INSTANCES = "many_instances"
    COMPRESSED = "high_compression"


def mesh_xml(mesh: Mesh, object_id: int = 1) -> str:
    vertices = "".join(
        f'<vertex x="{x:.17g}" y="{y:.17g}" z="{z:.17g}"/>' for x, y, z in mesh.vertices
    )
    triangles = "".join(
        f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in mesh.faces
    )
    return (
        f'<object id="{object_id}" type="model"><mesh><vertices>{vertices}</vertices>'
        f"<triangles>{triangles}</triangles></mesh></object>"
    )


def model_xml(
    resources: str,
    build: str,
    *,
    unit: str = "millimeter",
    attributes: str = "",
    metadata: str = "",
    build_attributes: str = "",
) -> str:
    return (
        f'<model xmlns="{CORE}" unit="{unit}"{attributes}>{metadata}'
        f"<resources>{resources}</resources><build{build_attributes}>{build}</build></model>"
    )


def archive(
    members: dict[str, bytes],
    *,
    root_path: str = "/3D/3dmodel.model",
    compressed: bool = False,
    preview: bool = False,
) -> bytes:
    relationships = (
        f'<Relationship Id="root" Target="{escape(root_path)}" Type="{ROOT_TYPE}"/>'
        + (
            '<Relationship Id="preview" Target="/Metadata/thumbnail.png" '
            'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/>'
            if preview
            else ""
        )
    )
    all_members = {
        "[Content_Types].xml": b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/><Default Extension="png" ContentType="image/png"/><Default Extension="config" ContentType="application/xml"/></Types>',
        "_rels/.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + relationships
            + "</Relationships>"
        ).encode(),
        **members,
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as package:
        for name, content in sorted(all_members.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = (
                zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED
            )
            package.writestr(info, content)
    return output.getvalue()


def scene(case: SceneCase, *, unit: str = "millimeter") -> bytes:
    """Source variations isolate one declared capability, bounded below 2 MiB."""
    edge = 20 / UNIT_SCALE[unit]
    mesh = box(size=(edge, edge, edge))
    if case is SceneCase.EMPTY:
        mesh = Mesh((), ())
    elif case is SceneCase.INVALID:
        mesh = Mesh(mesh.vertices, ((0, 1, 999), *mesh.faces[1:]))
    elif case is SceneCase.SOURCE_TRANSLATION:
        mesh = box(offset=(1e12, 1e12, 1e12))
    elif case is SceneCase.LARGE_SCALE:
        mesh = box(size=(1e6, 1e6, 1e6))
    elif case is SceneCase.PERMUTED:
        mesh = permute(mesh)
    resources = mesh_xml(mesh)
    build = '<item objectid="1"/>'
    attributes = ""
    metadata = ""
    build_attributes = ""
    extras: dict[str, bytes] = {}
    root_path = "/3D/3dmodel.model"
    if case is SceneCase.ROOT_RELATIONSHIP:
        root_path = "/3D/reachable.model"
        extras["3D/3dmodel.model"] = model_xml(
            mesh_xml(box(size=(999, 999, 999))), build
        ).encode()
    elif case is SceneCase.DUPLICATE_ID:
        resources += mesh_xml(mesh)
    elif case is SceneCase.UNREFERENCED:
        resources += mesh_xml(box(size=(999, 999, 999)), 2)
        extras["3D/unused.model"] = model_xml(mesh_xml(box()), build).encode()
    elif case is SceneCase.PRINTABLE:
        build += '<item objectid="1" printable="false" transform="1 0 0 0 1 0 0 0 1 1000 0 0"/>'
    elif case is SceneCase.INTERNAL_PART:
        attributes = f' xmlns:p="{PRODUCTION}" requiredextensions="p"'
        resources = (
            '<object id="2" p:UUID="11111111-1111-4111-8111-111111111111" type="model">'
            '<components><component objectid="1" p:path="/3D/parts/part.model" '
            'p:UUID="22222222-2222-4222-8222-222222222222"/></components></object>'
        )
        build = '<item objectid="2" p:UUID="33333333-3333-4333-8333-333333333333"/>'
        build_attributes = ' p:UUID="44444444-4444-4444-8444-444444444444"'
        part_resource = mesh_xml(mesh).replace(
            '<object id="1"',
            '<object id="1" p:UUID="55555555-5555-4555-8555-555555555555"',
        )
        extras["3D/parts/part.model"] = model_xml(
            part_resource, "", attributes=attributes
        ).encode()
        extras["3D/_rels/3dmodel.model.rels"] = (
            f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="part" Target="/3D/parts/part.model" Type="{ROOT_TYPE}"/></Relationships>'
        ).encode()
    elif case is SceneCase.NESTED:
        resources += '<object id="2" type="model"><components><component objectid="1" transform="1 0 0 0 1 0 0 0 1 10 0 0"/></components></object><object id="3" type="model"><components><component objectid="2" transform="1 0 0 0 1 0 0 0 1 20 0 0"/></components></object>'
        build = '<item objectid="3" transform="1 0 0 0 1 0 0 0 1 40 0 0"/><item objectid="1"/>'
    elif case is SceneCase.CYCLE:
        resources += '<object id="2" type="model"><components><component objectid="3"/></components></object><object id="3" type="model"><components><component objectid="2"/></components></object>'
        build = '<item objectid="2"/>'
    elif case is SceneCase.REFLECTION:
        build = '<item objectid="1" transform="-1 0 0 0 1 0 0 0 1 0 0 0"/><item objectid="1" transform="1 0 0 0 1 0 0 0 1 40 0 0"/>'
    elif case is SceneCase.SINGULAR:
        build = '<item objectid="1" transform="1 0 0 0 1 0 0 0 0 0 0 0"/>'
    elif case is SceneCase.EXPONENTIAL:
        for i in range(2, 23):
            resources += f'<object id="{i}" type="model"><components><component objectid="{i - 1}"/><component objectid="{i - 1}"/></components></object>'
        build = '<item objectid="22"/>'
    elif case is SceneCase.UNKNOWN_REQUIRED:
        attributes = ' xmlns:future="https://example.invalid/unimplemented" requiredextensions="future"'
    elif case is SceneCase.INSTANCES:
        build = "".join(
            f'<item objectid="1" transform="1 0 0 0 1 0 0 0 1 {i * 40} 0 0"/>'
            for i in range(2048)
        )
    elif case is SceneCase.PLATES:
        build += '<item objectid="1" transform="1 0 0 0 1 0 0 0 1 40 0 0"/>'
        extras["Metadata/model_settings.config"] = (
            b'<config><plate id="1"><model_instance object_id="1"/></plate><plate id="2"><model_instance object_id="1"/></plate></config>'
        )
    elif case is SceneCase.AUXILIARY:
        resources += mesh_xml(box(size=(1, 1, 1), offset=(100, 0, 0)), 2)
        build += '<item objectid="2"/>'
    elif case is SceneCase.FOREIGN_METADATA:
        metadata = '<metadata name="synthetic:slicer-version">foreign test value</metadata><metadata name="other:ignored">unrelated metadata</metadata>'
    elif case is SceneCase.COMPRESSED:
        metadata = "<!--" + "bounded padding " * 65536 + "-->"
    if case in (SceneCase.PREVIEW_VALID, SceneCase.PREVIEW_BROKEN):
        extras["Metadata/thumbnail.png"] = (
            PNG if case is SceneCase.PREVIEW_VALID else b"not a png"
        )
    extras[root_path.lstrip("/")] = model_xml(
        resources,
        build,
        unit=unit,
        attributes=attributes,
        metadata=metadata,
        build_attributes=build_attributes,
    ).encode()
    return archive(
        extras,
        root_path=root_path,
        compressed=case is SceneCase.COMPRESSED,
        preview=case in (SceneCase.PREVIEW_VALID, SceneCase.PREVIEW_BROKEN),
    )
