"""Unit identities preserve Artifact/component keys independently of an ORM."""

import pytest

from printstash_core.inference import EmbeddingError
from printstash_core.inference.units import unit_component, unit_key


class TestUnitIdentity:
    @pytest.mark.parametrize(
        "file_id,component,digest",
        [
            (0, 0, "a" * 64),
            (2**63, 0, "a" * 64),
            (1, -1, "a" * 64),
            (1, 2049, "a" * 64),
            (1, 0, "bad"),
        ],
    )
    def test_refuses_invalid_unit_identity(self, file_id, component, digest):
        with pytest.raises(EmbeddingError, match="embedding_unit_invalid"):
            unit_key(file_id, component, digest, "{}")

    @pytest.mark.parametrize(
        "key", ["invalid", "mesh:1:2049:" + "a" * 64 + ":" + "a" * 16]
    )
    def test_refuses_unknown_component_encoding(self, key):
        assert unit_component(key) is None

    @pytest.mark.parametrize("file_id,component", [(1, 0), (2**63 - 1, 2048)])
    def test_preserves_valid_artifact_component_identity(self, file_id, component):
        key = unit_key(file_id, component, "a" * 64, "{}")
        assert key == f"mesh:{file_id}:{component}:" + "a" * 64 + ":44136fa355b3678a"
        assert unit_component(key) == component
