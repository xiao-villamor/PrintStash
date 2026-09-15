"""Mesh jobs fail explicitly when their required Rust engine cannot load."""

import importlib
from types import SimpleNamespace

import numpy as np
import pytest

from printstash_core.mesh import native_rasterizer, render_mesh_thumbnail, stl, threemf
from printstash_core.mesh.native_geometry import measure_mesh


@pytest.mark.parametrize("missing", ["printstash_mesh_native", "broken_dependency"])
def test_native_import_failure_propagates(monkeypatch, missing):
    original = importlib.import_module

    def load(name):
        if name == "printstash_mesh_native":
            raise ModuleNotFoundError("required native dependency", name=missing)
        return original(name)

    monkeypatch.setattr(importlib, "import_module", load)
    with pytest.raises(ModuleNotFoundError) as error:
        native_rasterizer.kernel()
    assert error.value.name == missing


@pytest.mark.parametrize("operation", ["preview", "stl", "3mf", "geometry"])
def test_missing_native_cannot_return_python_output(monkeypatch, tmp_path, operation):
    original = importlib.import_module

    def load(name):
        if name == "printstash_mesh_native":
            raise ModuleNotFoundError("required native dependency", name=name)
        return original(name)

    monkeypatch.setattr(importlib, "import_module", load)
    mesh = SimpleNamespace(vertices=np.eye(3), faces=np.array([[0, 1, 2]]))
    with pytest.raises(ModuleNotFoundError, match="required native dependency"):
        if operation == "preview":
            render_mesh_thumbnail(mesh, "model", width=32, height=32)
        elif operation == "stl":
            stl.load_binary_stl(tmp_path / "model.stl")
        elif operation == "3mf":
            threemf.load_scene(tmp_path / "model.3mf")
        else:
            measure_mesh(mesh)


def test_python_render_callback_is_not_an_api_option():
    with pytest.raises(TypeError, match="rasterise_triangles"):
        render_mesh_thumbnail(None, "model", rasterise_triangles=lambda: None)
