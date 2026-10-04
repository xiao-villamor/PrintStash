"""Tagged worker values preserve data types and reject malformed tag payloads.

A tuple tag carries a JSON list and a bytes tag carries validated base64 text;
arbitrary tag objects cannot become plausible descriptor values.
"""

import pytest

from app.modules.media.mesh_wire_values import pack_value, unpack_value


class TestPackValue:
    def test_preserves_supported_descriptor_values(self):
        values = {"blob": b"shape", "tuple": (1, 2.0), "nested": [{"value": None}]}

        decoded = unpack_value(pack_value(values))

        assert decoded == values
        assert type(decoded["blob"]) is bytes
        assert type(decoded["tuple"]) is tuple


class TestUnpackValue:
    @pytest.mark.parametrize(
        "raw",
        [
            {"$tuple": "abc"},
            {"$tuple": {"x": 1}},
            {"$tuple": None},
            {"$tuple": 1},
            {"$bytes": None},
            {"$bytes": 1},
            {"$bytes": "!bad"},
            {"$unknown": []},
        ],
        ids=repr,
    )
    def test_rejects_invalid_tag_payload(self, raw):
        with pytest.raises((ValueError, TypeError)):
            unpack_value(raw)


class TestPackRejectsInvalidValues:
    @pytest.mark.parametrize(
        "value",
        [object(), {"$bytes": "AAAA"}, {"$tuple": []}, float("nan"), float("inf")],
        ids=repr,
    )
    def test_rejects_unsafe_descriptor_value(self, value):
        with pytest.raises((ValueError, TypeError)):
            pack_value(value)
