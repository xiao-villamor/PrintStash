"""Exercise the optional pilot against real XML/native readers, without adoption."""

from importlib import import_module
from pathlib import Path

import numpy as np
import pytest

from app.modules.media import mesh_resources, three_mf_scene
from scripts.pilot_lib3mf import _arrays, check_expectations, enabled_backends, measure
from tests.factories.three_mf_pilot import load_case, load_mesh


@pytest.fixture(params=enabled_backends())
def backend(request):
    return request.param


class TestPilotWorker:
    def test_materializes_declared_load_geometry(self, tmp_path, backend):
        case = load_mesh(12, instances=3)
        path = tmp_path / "copies.3mf"
        path.write_bytes(case.payload)

        observed = measure(path, backend)

        assert check_expectations(observed, case.expected) == []
        assert (
            observed["unique_array_bytes"] * 3 == observed["materialized_array_bytes"]
        )

    def test_preserves_nested_physical_placement(self, tmp_path, backend):
        case = load_case("nested-reflection")
        path = tmp_path / "nested.3mf"
        path.write_bytes(case.payload)

        observed = measure(path, backend)

        assert check_expectations(observed, case.expected) == []

    @pytest.mark.parametrize(
        "name", ["reachable-cycle", "expanded-budget", "invalid-face-index"]
    )
    def test_records_refused_input(self, tmp_path, backend, name):
        case = load_case(name)
        path = tmp_path / "refusal.3mf"
        path.write_bytes(case.payload)

        observed = measure(path, backend, max_faces=case.max_faces)

        assert observed["outcome"] == "refused"
        assert observed["error"]

    def test_retains_costs_after_refusal(self, tmp_path, backend):
        case = load_case("expanded-budget")
        path = tmp_path / "budget.3mf"
        path.write_bytes(case.payload)

        observed = measure(path, backend, max_faces=case.max_faces)

        assert observed["outcome"] == "refused"
        assert observed["total_ms"] > 0
        assert observed["import_ms"] >= 0
        assert observed["read_ms"] >= 0
        assert observed["peak_rss_bytes"] > 0


class TestCurrentInstrumentation:
    @pytest.mark.parametrize("name", ["core-basic", "expanded-budget"])
    def test_restores_current_reader_seams(self, tmp_path, name):
        case = load_case(name)
        path = tmp_path / "source.3mf"
        path.write_bytes(case.payload)
        originals = (
            three_mf_scene._attribute_columns,
            three_mf_scene.expand_scene,
            mesh_resources.compose_scene,
        )

        measure(path, "current", max_faces=case.max_faces)

        assert (
            three_mf_scene._attribute_columns,
            three_mf_scene.expand_scene,
            mesh_resources.compose_scene,
        ) == originals


# Register only applicable research cases; current-reader cases always exist.
if "lib3mf-public" in enabled_backends():
    class TestNativeArrays:
        def test_buffer_extraction_matches_public_arrays(self, tmp_path):
            binding = import_module("lib3mf.Lib3MF")

            path = tmp_path / "arrays.3mf"
            path.write_bytes(load_mesh(20).payload)
            wrapper = binding.Wrapper(str(Path(binding.__file__).with_name("lib3mf")))
            model = wrapper.CreateModel()
            model.QueryReader("3mf").ReadFromFile(str(path))
            meshes = model.GetMeshObjects()
            assert meshes.MoveNext()
            mesh = meshes.GetCurrent()

            public = _arrays(mesh, binding, "lib3mf-public", np)
            buffer = _arrays(mesh, binding, "lib3mf-buffer", np)

            np.testing.assert_array_equal(buffer[0], public[0])
            np.testing.assert_array_equal(buffer[1], public[1])
            assert buffer[0].dtype == np.float64
            assert buffer[1].dtype == np.int64
            assert buffer[0].flags.owndata
            assert buffer[1].flags.owndata
