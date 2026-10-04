"""Direct 3MF consumers preserve typed source refusals."""

import pytest

from app.modules.media import mesh_loading
from app.modules.media.three_mf_scene import Unsupported3MFCapability
from tests.factories.three_mf_pilot import load_case


@pytest.fixture
def unsupported_3mf(tmp_path):
    source = tmp_path / "required.3mf"
    source.write_bytes(load_case("unknown-required-extension").payload)
    return source


class TestLoad3mfMesh:
    def test_preserves_unsupported_capability(self, unsupported_3mf):
        with pytest.raises(Unsupported3MFCapability) as error:
            mesh_loading.load_mesh(unsupported_3mf, file_type="3mf")

        assert error.value.code == "unsupported_3mf_capability"
        assert error.value.namespace == "urn:printstash:unknown"


class TestToStlBytes:
    def test_preserves_unsupported_capability(self, unsupported_3mf):
        with pytest.raises(Unsupported3MFCapability) as error:
            mesh_loading.to_stl_bytes(unsupported_3mf, file_type="3mf")

        assert error.value.code == "unsupported_3mf_capability"
