"""Production mesh consumers must enter through the bounded isolation seams."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from importlib.util import resolve_name

import pytest

from tests.paths import BACKEND_DIR

# These modules implement loading/rendering or are disposable worker entry points.
# A new consumer must use mesh_isolation, stl_isolation, verification_isolation,
# embedding_isolation or visual_render rather than extending this list.
OWNERS = {
    "modules/media/mesh_processing.py",
    "modules/media/mesh_loading.py",
    "modules/media/mesh_measurements.py",
    "modules/media/scene_measurements.py",
    "modules/media/mesh_policy.py",
    "modules/media/mesh_previews.py",
    "modules/media/mesh_resources.py",
    "modules/media/three_mf_scene.py",
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

# Array, scene, render and transport data owners cannot acquire an orchestrator
# dependency even for annotations. New data contracts belong in mesh_contracts.
PRIMITIVE_OWNERS = {
    "modules/media/mesh_loading.py",
    "modules/media/mesh_measurements.py",
    "modules/media/scene_measurements.py",
    "modules/media/mesh_policy.py",
    "modules/media/mesh_previews.py",
    "modules/media/mesh_contracts.py",
    "modules/media/mesh_resources.py",
    "modules/media/three_mf_scene.py",
    "modules/media/mesh_render.py",
    "modules/media/mesh_telemetry.py",
    "modules/media/fingerprints.py",
    "modules/media/stl_fallback.py",
    "modules/media/stl_streaming.py",
}
CONTRACT_TYPES = {
    "Geometry",
    "MeshMeasurements",
    "ProgressReporter",
    "ThumbnailStrategy",
    "ThumbnailFailureReason",
    "GeometryReady",
    "GeometryRefused",
    "GeometryNotRequested",
    "GeometryOutcome",
    "ThumbnailRequest",
    "ThumbnailResult",
    "ThumbnailMetricsSink",
}
ORCHESTRATORS = {
    "app.modules.media.thumbnail_engine",
    "app.modules.media.geometry_analysis",
    "app.modules.media.mesh_processing",
}

RAW = {
    "printstash_core.mesh.render_geometry.prepare_mesh_render",
    "printstash_core.mesh.render_geometry.prepare_scene_render",
    "printstash_core.mesh.rasterizer.render_prepared_pixels",
    "printstash_core.mesh.rasterizer.render_prepared_thumbnail",
    "printstash_core.mesh.prepare_mesh_render",
    "printstash_core.mesh.prepare_scene_render",
    "printstash_core.mesh.render_prepared_pixels",
    "printstash_core.mesh.render_prepared_thumbnail",
    "app.modules.media.mesh_render.prepare_mesh_render",
    "app.modules.media.mesh_render.prepare_scene_render",
    "app.modules.media.mesh_render.render_prepared_pixels",
    "app.modules.media.mesh_render.render_prepared_thumbnail",
    "printstash_core.mesh.similarity.fingerprint_mesh",
    "printstash_core.mesh.similarity.geometry.prepare_surface",
    "printstash_core.mesh.similarity.verification.verify_meshes",
    "printstash_core.mesh.rasterizer.render_mesh_thumbnail",
    "printstash_core.mesh.rasterizer.render_scene_thumbnail",
    "printstash_core.mesh.render_scene_thumbnail",
    "app.modules.media.fingerprints.fingerprint_mesh",
    "app.modules.media.fingerprints.fingerprint_path",
    "app.modules.media.thumbnail_engine.ThumbnailEngine",
    "app.modules.media.mesh_loading.load_mesh",
    "app.modules.media.mesh_loading.load_step_mesh",
    "app.modules.media.mesh_loading.to_stl_bytes",
    "app.modules.media.mesh_measurements.geometry_from_mesh",
    "app.modules.media.mesh_measurements.signed_mesh_integral",
    "app.modules.media.scene_measurements.measure_scene",
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
    "app.modules.media.mesh_resources.materialize_scene",
    "app.modules.media.three_mf_scene.read_scene",
    "app.modules.media.mesh_resources.prepare_loaded_mesh",
    "app.modules.media.mesh_render.render_thumbnail",
    "app.modules.media.mesh_render.render_mesh_thumbnail",
    "app.modules.media.mesh_render.render_scene_thumbnail",
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


def _orchestrator_dependencies(source: str, package: str) -> list[str]:
    dependencies = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            targets = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = (
                resolve_name("." * node.level + (node.module or ""), package)
                if node.level
                else node.module or ""
            )
            targets = [f"{module}.{alias.name}" for alias in node.names]
        else:
            continue
        dependencies.extend(
            target
            for target in targets
            if any(
                target == owner or target.startswith(owner + ".")
                for owner in ORCHESTRATORS
            )
        )
    return dependencies


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
            "from .mesh_loading import load_mesh",
            "from .mesh_measurements import geometry_from_mesh",
            "from app.modules.media import mesh_loading; mesh_loading.to_stl_bytes(p)",
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

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (source, target)
            for target in (
                "app.modules.media.scene_measurements.measure_scene",
                "app.modules.media.mesh_resources.materialize_scene",
                "app.modules.media.mesh_measurements.signed_mesh_integral",
                "app.modules.media.mesh_render.render_scene_thumbnail",
                "printstash_core.mesh.rasterizer.render_scene_thumbnail",
                "printstash_core.mesh.render_scene_thumbnail",
            )
            for module, name in (target.rsplit(".", 1),)
            for source in (
                f"from {module} import {name} as direct",
                f"import {module} as owner\nowner.{name}(source)",
                f"import {module} as owner\ncallback = owner.{name}",
            )
        ],
    )
    def test_rejects_retained_scene_bypasses(self, source, target):
        assert target in _violations(source, "app.modules.media")

    @pytest.mark.parametrize(
        ("source", "target"),
        [
            (source, target)
            for target in (
                "printstash_core.mesh.render_geometry.prepare_mesh_render",
                "printstash_core.mesh.render_geometry.prepare_scene_render",
                "printstash_core.mesh.rasterizer.render_prepared_pixels",
                "printstash_core.mesh.rasterizer.render_prepared_thumbnail",
                "printstash_core.mesh.prepare_mesh_render",
                "printstash_core.mesh.prepare_scene_render",
                "printstash_core.mesh.render_prepared_pixels",
                "printstash_core.mesh.render_prepared_thumbnail",
                "app.modules.media.mesh_render.prepare_mesh_render",
                "app.modules.media.mesh_render.prepare_scene_render",
                "app.modules.media.mesh_render.render_prepared_pixels",
                "app.modules.media.mesh_render.render_prepared_thumbnail",
            )
            for module, name in (target.rsplit(".", 1),)
            for source in (
                f"from {module} import {name} as direct",
                f"import {module} as owner\nowner.{name}(source)",
                f"import {module} as owner\ncallback = owner.{name}",
            )
        ],
    )
    def test_rejects_prepared_render_bypasses(self, source, target):
        assert target in _violations(source, "app.modules.media")

    def test_allows_safe_consumers(self):
        assert (
            _violations(
                "from .mesh_isolation import generate\n"
                "from .mesh_contracts import ThumbnailRequest\n"
                "from .fingerprints import FingerprintResult\n"
                "from printstash_core.mesh.similarity import GeometryError\n"
                "generate(request)",
                "app.modules.media",
            )
            == []
        )


class TestPrimitiveDependencies:
    def test_internal_consumers_import_contracts_from_the_data_owner(self):
        violations = []
        for directory in (
            BACKEND_DIR / "app",
            BACKEND_DIR / "tests",
            BACKEND_DIR / "scripts",
        ):
            for path in sorted(directory.rglob("*.py")):
                package = ".".join(path.parent.relative_to(BACKEND_DIR).parts)
                violations.extend(
                    f"{path.relative_to(BACKEND_DIR)}: {target}"
                    for target in _orchestrator_dependencies(path.read_text(), package)
                    if target.rpartition(".")[0] == "app.modules.media.thumbnail_engine"
                    and target.rpartition(".")[2] in CONTRACT_TYPES
                )
        assert violations == [], "\n".join(violations)

    def test_primitive_owners_do_not_import_orchestrators(self):
        app = BACKEND_DIR / "app"
        violations = []
        for relative in sorted(PRIMITIVE_OWNERS):
            path = app / relative
            package = "app." + path.parent.relative_to(app).as_posix().replace("/", ".")
            violations.extend(
                f"{relative}: {target}"
                for target in _orchestrator_dependencies(path.read_text(), package)
            )
        assert violations == [], "\n".join(violations)

    @pytest.mark.parametrize(
        "source",
        [
            "from app.modules.media.thumbnail_engine import ThumbnailRequest",
            "import app.modules.media.thumbnail_engine as engine",
            "from .thumbnail_engine import ThumbnailResult as Result",
            "from . import thumbnail_engine as engine",
            "from ..media.thumbnail_engine import GeometryReady",
            "from app.modules.media import thumbnail_engine",
            "from .thumbnail_engine import *",
            "if TYPE_CHECKING:\n    from .thumbnail_engine import ThumbnailFailureReason",
            "from .geometry_analysis import VisualViews",
            "from .mesh_processing import _load_mesh",
            "from . import mesh_processing",
        ],
    )
    def test_rejects_orchestrator_imports(self, source):
        assert _orchestrator_dependencies(source, "app.modules.media")

    @pytest.mark.parametrize(
        "source",
        [
            "from .mesh_contracts import ThumbnailRequest",
            "if TYPE_CHECKING:\n    from .mesh_contracts import ThumbnailResult",
            "from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES",
        ],
    )
    def test_accepts_data_contract_imports(self, source):
        assert _orchestrator_dependencies(source, "app.modules.media") == []

    def test_contract_import_does_not_load_execution_owners(self):
        program = "import json, sys; import app.modules.media.mesh_contracts; print(json.dumps({name: name in sys.modules for name in ('app.modules.media.thumbnail_engine', 'app.modules.media.geometry_analysis', 'app.modules.media.mesh_resources', 'app.modules.media.fingerprints')}))"

        result = subprocess.run(
            [sys.executable, "-c", program],
            cwd=BACKEND_DIR,
            check=True,
            text=True,
            capture_output=True,
        )

        assert json.loads(result.stdout) == {
            "app.modules.media.thumbnail_engine": False,
            "app.modules.media.geometry_analysis": False,
            "app.modules.media.mesh_resources": False,
            "app.modules.media.fingerprints": False,
        }


# The compatibility facade has one fixed test consumer and no production ones.
# Shrinking this inventory is allowed; adding a consumer requires using an owner.
FACADE_CONSUMERS = {
    "tests/unit/modules/media/mesh_processing/test_entry_points.py",
}


class TestMeshFacadeInventory:
    def test_only_fixed_legacy_consumers_import_the_facade(self):
        consumers = set()
        for directory in (
            BACKEND_DIR / "app",
            BACKEND_DIR / "tests",
            BACKEND_DIR / "scripts",
        ):
            for path in directory.rglob("*.py"):
                package = ".".join(path.parent.relative_to(BACKEND_DIR).parts)
                if any(
                    target == "app.modules.media.mesh_processing"
                    or target.startswith("app.modules.media.mesh_processing.")
                    for target in _orchestrator_dependencies(path.read_text(), package)
                ):
                    consumers.add(path.relative_to(BACKEND_DIR).as_posix())
        assert consumers <= FACADE_CONSUMERS, "\n".join(
            sorted(consumers - FACADE_CONSUMERS)
        )

    def test_facade_holds_no_mutable_policy_or_native_implementation(self):
        path = BACKEND_DIR / "app/modules/media/mesh_processing.py"
        tree = ast.parse(path.read_text())
        assert {
            n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))
        } == {"extract_geometry"}
        assert {
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        } == {"__all__"}
        assert set(_violations(path.read_text(), "app.modules.media")) <= {
            "app.modules.media.mesh_loading.load_mesh",
            "app.modules.media.mesh_loading.load_step_mesh",
            "app.modules.media.mesh_loading.to_stl_bytes",
            "app.modules.media.mesh_measurements.geometry_from_mesh",
        }
