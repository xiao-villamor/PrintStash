"""Production mesh consumers must enter through the bounded isolation seams."""

from __future__ import annotations

import ast

from tests.paths import BACKEND_DIR

# These modules implement loading/rendering or are disposable worker entry points.
# A new consumer must use mesh_isolation, stl_isolation, verification_isolation,
# embedding_isolation or visual_render rather than extending this list.
OWNERS = {
    "modules/media/mesh_processing.py",
    "modules/media/mesh_resources.py",
    "modules/media/thumbnail_engine.py",
    "modules/media/geometry_analysis.py",
    "modules/media/mesh_worker.py",
    "modules/media/step_worker.py",
    "modules/media/stl_worker.py",
    "modules/media/visual_worker.py",
    "modules/media/embedding_worker.py",
    "modules/media/verification_worker.py",
}

RAW = {
    "app.modules.media.thumbnail_engine.ThumbnailEngine",
    "app.modules.media.mesh_processing._load_mesh",
    "app.modules.media.mesh_processing._load_step_mesh_isolated",
    "app.modules.media.mesh_processing.extract_geometry",
    "app.modules.media.mesh_processing.to_stl_bytes",
    "app.modules.media.geometry_analysis.visual_views",
    "app.modules.media.geometry_analysis.prepare",
    "app.modules.media.geometry_analysis.analyze",
    "app.modules.media.mesh_resources.load_3mf",
}


def test_new_consumers_cannot_bypass_mesh_isolation():
    violations = []
    app = BACKEND_DIR / "app"
    for path in sorted(app.rglob("*.py")):
        relative = path.relative_to(app).as_posix()
        if relative in OWNERS:
            continue
        aliases = {}
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "trimesh":
                        violations.append(f"{relative}:{node.lineno}: trimesh")
                    aliases[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    target = f"{node.module}.{alias.name}"
                    if node.module.startswith("trimesh") or target in RAW:
                        violations.append(f"{relative}:{node.lineno}: {target}")
                    aliases[alias.asname or alias.name] = target
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            parts = []
            expression = node.func
            while isinstance(expression, ast.Attribute):
                parts.append(expression.attr)
                expression = expression.value
            if isinstance(expression, ast.Name):
                target = ".".join(
                    [aliases.get(expression.id, expression.id), *reversed(parts)]
                )
                if target in RAW:
                    violations.append(f"{relative}:{node.lineno}: {target}")
    assert violations == [], "\n".join(violations)
