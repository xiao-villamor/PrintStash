"""Production mesh consumers must enter through the bounded isolation seams."""

from __future__ import annotations

import ast
from importlib.util import resolve_name

import pytest

from tests.paths import BACKEND_DIR

# These modules implement loading/rendering or are disposable worker entry points.
# A new consumer must use mesh_isolation, stl_isolation, verification_isolation,
# embedding_isolation or visual_render rather than extending this list.
OWNERS = {
    "modules/media/mesh_processing.py",
    "modules/media/mesh_resources.py",
    "modules/media/thumbnail_engine.py",
    "modules/media/geometry_analysis.py",
    "modules/media/fingerprints.py",
    "modules/media/mesh_render.py",
    "modules/media/step_geometry.py",
    "modules/media/stl_fallback.py",
    "modules/media/mesh_worker.py",
    "modules/media/step_worker.py",
    "modules/media/stl_worker.py",
    "modules/media/visual_worker.py",
    "modules/media/embedding_worker.py",
    "modules/media/verification_worker.py",
}

RAW = {
    "printstash_core.mesh.similarity.fingerprint_mesh",
    "printstash_core.mesh.similarity.geometry.prepare_surface",
    "printstash_core.mesh.similarity.verification.verify_meshes",
    "printstash_core.mesh.rasterizer.render_mesh_thumbnail",
    "app.modules.media.fingerprints.fingerprint_mesh",
    "app.modules.media.fingerprints.fingerprint_path",
    "app.modules.media.thumbnail_engine.ThumbnailEngine",
    "app.modules.media.mesh_processing._load_mesh",
    "app.modules.media.mesh_processing._load_step_mesh_isolated",
    "app.modules.media.mesh_processing.extract_geometry",
    "app.modules.media.mesh_processing.to_stl_bytes",
    "app.modules.media.geometry_analysis.visual_views",
    "app.modules.media.geometry_analysis._load",
    "app.modules.media.geometry_analysis.verify_paths",
    "app.modules.media.geometry_analysis.embedding_views",
    "app.modules.media.geometry_analysis.analyze",
    "app.modules.media.mesh_resources.load_3mf",
    "app.modules.media.mesh_resources.prepare_loaded_mesh",
    "app.modules.media.mesh_render.render_thumbnail",
    "app.modules.media.mesh_render.render_mesh_thumbnail",
    "app.modules.media.step_geometry.tessellate",
    "app.modules.media.step_worker.convert",
    "app.modules.media.fingerprints.extract",
    "app.modules.media.stl_fallback.sample_stl_geometry",
    "app.modules.media.stl_fallback.render_stl_thumbnail",
}


def _native(target: str) -> bool:
    return target.split(".", 1)[0] in {"trimesh", "OCP", "cascadio"}


def _violations(source: str, package: str) -> list[str]:
    aliases = {}
    violations = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _native(alias.name):
                    violations.append(alias.name)
                bound = alias.asname or alias.name.split(".", 1)[0]
                aliases[bound] = alias.name if alias.asname else bound
        elif isinstance(node, ast.ImportFrom):
            module = (
                resolve_name("." * node.level + (node.module or ""), package)
                if node.level
                else node.module or ""
            )
            for alias in node.names:
                target = f"{module}.{alias.name}"
                if (
                    _native(module)
                    or target in RAW
                    or alias.name == "*"
                    and any(raw.startswith(module + ".") for raw in RAW)
                ):
                    violations.append(target)
                aliases[alias.asname or alias.name] = target
    for node in ast.walk(tree):
        expression = node.func if isinstance(node, ast.Call) else node
        if not isinstance(expression, ast.Attribute):
            continue
        parts = []
        while isinstance(expression, ast.Attribute):
            parts.append(expression.attr)
            expression = expression.value
        if isinstance(expression, ast.Name):
            target = ".".join(
                [aliases.get(expression.id, expression.id), *reversed(parts)]
            )
            if target in RAW:
                violations.append(target)
    return violations


class TestMeshBoundaries:
    def test_new_consumers_cannot_bypass_mesh_isolation(self):
        violations = []
        app = BACKEND_DIR / "app"
        for path in sorted(app.rglob("*.py")):
            relative = path.relative_to(app).as_posix()
            if relative in OWNERS:
                continue
            package = "app." + path.relative_to(app).parent.as_posix().replace("/", ".")
            violations.extend(
                f"{relative}: {target}"
                for target in _violations(path.read_text(), package.rstrip("."))
            )
        assert violations == [], "\n".join(violations)

    @pytest.mark.parametrize(
        "source",
        [
            "import trimesh.exchange.stl",
            "from trimesh import load",
            "from OCP.STEPControl import STEPControl_Reader",
            "import cascadio as cad",
            "from .mesh_processing import extract_geometry",
            "from ..media.step_geometry import tessellate",
            "from .mesh_processing import *",
            "from . import mesh_processing as meshes; meshes.extract_geometry(p)",
            "from app.modules.media import mesh_render as render; render.render_thumbnail(p)",
            "import app.modules.media.mesh_processing; app.modules.media.mesh_processing._load_mesh(p)",
            "import app.modules.media.fingerprints as fp; callback = fp.extract",
        ],
    )
    def test_rejects_mesh_bypasses(self, source):
        assert _violations(source, "app.modules.media")

    def test_allows_safe_consumers(self):
        assert (
            _violations(
                "from .mesh_isolation import generate\n"
                "from .thumbnail_engine import ThumbnailRequest\n"
                "from .fingerprints import FingerprintResult\n"
                "from printstash_core.mesh.similarity import GeometryError\n"
                "generate(request)",
                "app.modules.media",
            )
            == []
        )
