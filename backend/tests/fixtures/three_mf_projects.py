"""Small standards-valid 3MF projects used by mesh conversion tests."""

from __future__ import annotations

import io
import json
import zipfile


def build_3d_builder_component_project() -> bytes:
    """Return a 3MF with a mesh, a component transform, and a build transform."""
    model = b"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US"
       xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>
    <object id="1" type="model" name="Part">
      <mesh>
        <vertices>
          <vertex x="0" y="0" z="0"/><vertex x="2" y="0" z="0"/>
          <vertex x="2" y="3" z="0"/><vertex x="0" y="3" z="0"/>
          <vertex x="0" y="0" z="4"/><vertex x="2" y="0" z="4"/>
          <vertex x="2" y="3" z="4"/><vertex x="0" y="3" z="4"/>
        </vertices>
        <triangles>
          <triangle v1="0" v2="1" v3="2"/><triangle v1="0" v2="2" v3="3"/>
          <triangle v1="4" v2="6" v3="5"/><triangle v1="4" v2="7" v3="6"/>
          <triangle v1="0" v2="4" v3="5"/><triangle v1="0" v2="5" v3="1"/>
          <triangle v1="1" v2="5" v3="6"/><triangle v1="1" v2="6" v3="2"/>
          <triangle v1="2" v2="6" v3="7"/><triangle v1="2" v2="7" v3="3"/>
          <triangle v1="4" v2="0" v3="3"/><triangle v1="4" v2="3" v3="7"/>
        </triangles>
      </mesh>
    </object>
    <object id="2" type="model" name="PlacedPart">
      <components>
        <component objectid="1" transform="1 0 0 0 1 0 0 0 1 10 20 30"/>
      </components>
    </object>
  </resources>
  <build>
    <item objectid="2" transform="1 0 0 0 1 0 0 0 1 100 200 300"/>
  </build>
</model>"""
    content_types = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
</Types>"""
    relationships = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="/3D/3dmodel.model"/>
</Relationships>"""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("3D/3dmodel.model", model)
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", relationships)
    return archive.getvalue()


def build_instanced_project(instances: int) -> bytes:
    """Return a 3MF placing one 12-triangle cube ``instances`` times.

    The XML stays a few KiB however large ``instances`` is, while the expanded
    scene has ``12 * instances`` faces. That is the shape of #259: a size-based
    triangle estimate sees a tiny file, and a loader that expands placements
    while parsing allocates for the whole scene.
    """
    components = "".join(
        f'<component objectid="1" transform="1 0 0 0 1 0 0 0 1 {index * 5} 0 0"/>'
        for index in range(instances)
    )
    model = f"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US"
       xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>
    <object id="1" type="model">
      <mesh>
        <vertices>
          <vertex x="0" y="0" z="0"/><vertex x="2" y="0" z="0"/>
          <vertex x="2" y="3" z="0"/><vertex x="0" y="3" z="0"/>
          <vertex x="0" y="0" z="4"/><vertex x="2" y="0" z="4"/>
          <vertex x="2" y="3" z="4"/><vertex x="0" y="3" z="4"/>
        </vertices>
        <triangles>
          <triangle v1="0" v2="1" v3="2"/><triangle v1="0" v2="2" v3="3"/>
          <triangle v1="4" v2="6" v3="5"/><triangle v1="4" v2="7" v3="6"/>
          <triangle v1="0" v2="4" v3="5"/><triangle v1="0" v2="5" v3="1"/>
          <triangle v1="1" v2="5" v3="6"/><triangle v1="1" v2="6" v3="2"/>
          <triangle v1="2" v2="6" v3="7"/><triangle v1="2" v2="7" v3="3"/>
          <triangle v1="4" v2="0" v3="3"/><triangle v1="4" v2="3" v3="7"/>
        </triangles>
      </mesh>
    </object>
    <object id="2" type="model"><components>{components}</components></object>
  </resources>
  <build><item objectid="2"/></build>
</model>""".encode()
    relationships = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="/3D/3dmodel.model"/>
</Relationships>"""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("3D/3dmodel.model", model)
        package.writestr("_rels/.rels", relationships)
    return archive.getvalue()


_CUBE_MESH = """<mesh>
        <vertices>
          <vertex x="-5" y="-5" z="0"/><vertex x="5" y="-5" z="0"/>
          <vertex x="5" y="5" z="0"/><vertex x="-5" y="5" z="0"/>
          <vertex x="-5" y="-5" z="10"/><vertex x="5" y="-5" z="10"/>
          <vertex x="5" y="5" z="10"/><vertex x="-5" y="5" z="10"/>
        </vertices>
        <triangles>
          <triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="3" v3="2"/>
          <triangle v1="4" v2="5" v3="6"/><triangle v1="4" v2="6" v3="7"/>
          <triangle v1="0" v2="1" v3="5"/><triangle v1="0" v2="5" v3="4"/>
          <triangle v1="1" v2="2" v3="6"/><triangle v1="1" v2="6" v3="5"/>
          <triangle v1="2" v2="3" v3="7"/><triangle v1="2" v2="7" v3="6"/>
          <triangle v1="3" v2="0" v3="4"/><triangle v1="3" v2="4" v3="7"/>
        </triangles>
      </mesh>"""
BAMBU_BED = ("0x0", "256x0", "256x256", "0x256")


def build_multi_plate_project(
    *,
    names: tuple[str, ...] = ("Main parts", "Wall mount"),
    placements: tuple[tuple[float, float], ...] = ((128.0, 128.0), (435.2, 128.0)),
    printable_area: tuple[str, ...] | None = BAMBU_BED,
    images: bool = True,
    model_settings: str | None = None,
    marker: str = "",
) -> bytes:
    """A Bambu Studio project with one 10 mm cube per plate.

    Plate *i* lists object ``i + 1``, which the build places at
    ``placements[i]``; the defaults put each cube in the middle of its plate
    on a 256 mm bed (the second plate starts at 307.2 mm). ``model_settings``
    replaces the generated plate metadata; ``marker`` makes the bytes unique.
    """
    from tests.factories.content import png

    objects = "".join(
        f'<object id="{index + 1}" type="model" name="Cube {index + 1}">{_CUBE_MESH}</object>'
        for index in range(len(placements))
    )
    items = "".join(
        f'<item objectid="{index + 1}" transform="1 0 0 0 1 0 0 0 1 {x} {y} 0"/>'
        for index, (x, y) in enumerate(placements)
    )
    model = f"""<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xml:lang="en-US"
       xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <metadata name="Application">BambuStudio-02.00.02.57</metadata>
  <metadata name="Title">{marker}</metadata>
  <resources>{objects}</resources>
  <build>{items}</build>
</model>"""
    plates = "".join(
        f"""<plate>
    <metadata key="plater_id" value="{index + 1}"/>
    <metadata key="plater_name" value="{name}"/>
    <metadata key="thumbnail_file" value="Metadata/plate_{index + 1}.png"/>
    <model_instance>
      <metadata key="object_id" value="{index + 1}"/>
      <metadata key="instance_id" value="0"/>
    </model_instance>
  </plate>"""
        for index, name in enumerate(names)
    )
    settings = (
        model_settings
        if model_settings is not None
        else f'<?xml version="1.0" encoding="UTF-8"?>\n<config>{plates}</config>'
    )
    content_types = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="png" ContentType="image/png"/>
</Types>"""
    relationships = b"""<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="/3D/3dmodel.model"/>
</Relationships>"""
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("3D/3dmodel.model", model)
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("_rels/.rels", relationships)
        package.writestr("Metadata/model_settings.config", settings)
        if printable_area is not None:
            package.writestr(
                "Metadata/project_settings.config",
                json.dumps({"printable_area": list(printable_area)}),
            )
        if images:
            for index in range(len(names)):
                package.writestr(
                    f"Metadata/plate_{index + 1}.png", png(width=4 + index, height=4)
                )
    return archive.getvalue()


def build_sliced_project(
    *,
    plate_numbers: tuple[int, ...] = (1, 2),
    marker: str = "sliced-plates",
    with_mesh: bool = False,
) -> bytes:
    """A sliced export with distinct toolpaths; source meshes are optional."""
    stream = io.BytesIO(build_multi_plate_project() if with_mesh else b"")
    with zipfile.ZipFile(stream, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        for number in plate_numbers:
            archive.writestr(
                f"Metadata/plate_{number}.gcode",
                f"; {marker} plate {number}\nG90\nM82\n"
                f"G1 X0 Y0 Z0.2\nG1 X{number * 10} Y0 E1\n"
                f"G1 X{number * 10} Y10 E2\nG1 Z0.4\nG1 X0 Y10 E3\n",
            )
    return stream.getvalue()
