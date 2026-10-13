"""Capability refusals preserve a specific required namespace as typed data."""

import pytest

from app.modules.media.three_mf_scene import Unsupported3MFCapability


class TestUnsupportedCapability:
    def test_retains_the_required_namespace(self):
        failure = Unsupported3MFCapability("urn:printstash:unknown")

        assert failure.namespace == "urn:printstash:unknown"
        assert failure.code == "unsupported_3mf_capability"

    @pytest.mark.parametrize("namespace", [None, "", False, 1, [], {}])
    def test_rejects_an_invalid_required_namespace(self, namespace):
        with pytest.raises(TypeError, match="invalid_capability_namespace"):
            Unsupported3MFCapability(namespace)
