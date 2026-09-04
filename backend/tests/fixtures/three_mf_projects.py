"""Small standards-valid 3MF projects used by mesh conversion tests."""

from __future__ import annotations

import io
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
