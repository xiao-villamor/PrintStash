"""Pending inputs retain complete source identity and closed admission bounds."""

import json

import pytest
from printstash_core.mesh.similarity.budgets import MAX_ANALYSIS_FACES

from app.db.models import FileType
from app.modules.derivatives.mesh_continuation_values import (
    FingerprintPlan,
    decode_source,
    encode_source,
)
from app.modules.derivatives.records import ArtifactSource


@pytest.fixture(params=[False, True], ids=["local", "external"])
def source(request):
    external = request.param
    return ArtifactSource(
        sha256="a" * 64,
        file_type=FileType.STL,
        original_filename="part.stl",
        is_external=external,
        path="models/part.stl",
        size_bytes=100,
        model_id=1,
        external_library_id=2 if external else None,
        source_key="part.stl" if external else None,
        source_mtime=123.5 if external else None,
        source_etag="etag" if external else None,
        source_version_id="version" if external else None,
    )


class TestFingerprintPlan:
    @pytest.mark.parametrize("cap", [100, MAX_ANALYSIS_FACES])
    def test_accepts_admission_boundaries(self, cap):
        assert FingerprintPlan("geometry-v1", cap).triangle_cap == cap

    @pytest.mark.parametrize("cap", [99, MAX_ANALYSIS_FACES + 1, True, None, 100.0])
    def test_rejects_invalid_face_cap(self, cap):
        with pytest.raises(ValueError, match="invalid_continuation_face_cap"):
            FingerprintPlan("geometry-v1", cap)

    @pytest.mark.parametrize("version", ["", "x" * 129, True, None, 7])
    def test_rejects_invalid_algorithm(self, version):
        with pytest.raises(ValueError, match="invalid_continuation_algorithm"):
            FingerprintPlan(version, 100)


class TestSourceSnapshot:
    def test_round_trips_complete_identity(self, source):
        encoded = encode_source(source)
        assert decode_source(encoded) == source
        assert encode_source(decode_source(encoded)) == encoded
        assert json.loads(encoded)["source"]["file_type"] == FileType.STL.value

    @pytest.mark.parametrize(
        "corruption", ["missing", "extra", "version-bool", "version-old", "not-object"]
    )
    def test_rejects_invalid_versioned_envelope(self, source, corruption):
        raw = json.loads(encode_source(source))
        if corruption == "missing":
            del raw["source"]
        elif corruption == "extra":
            raw["extra"] = True
        elif corruption == "version-bool":
            raw["version"] = True
        elif corruption == "version-old":
            raw["version"] = 2
        else:
            raw = []
        with pytest.raises(ValueError, match="invalid_continuation_source_version"):
            decode_source(json.dumps(raw))

    @pytest.mark.parametrize(
        "field",
        [
            "sha256",
            "file_type",
            "original_filename",
            "is_external",
            "path",
            "size_bytes",
            "model_id",
            "external_library_id",
            "source_key",
            "source_mtime",
            "source_etag",
            "source_version_id",
        ],
    )
    def test_requires_even_nullable_source_attributes(self, source, field):
        raw = json.loads(encode_source(source))
        del raw["source"][field]
        with pytest.raises(ValueError, match="invalid_continuation_source_shape"):
            decode_source(json.dumps(raw))

    @pytest.mark.parametrize(
        "field,value",
        [
            ("sha256", "b" * 63),
            ("sha256", "G" * 64),
            ("original_filename", ""),
            ("path", None),
            ("size_bytes", -1),
            ("size_bytes", True),
            ("model_id", 0),
            ("is_external", 1),
            ("external_library_id", 0),
            ("source_key", []),
            ("source_etag", 1),
            ("source_version_id", True),
            ("source_mtime", float("nan")),
            ("source_mtime", float("inf")),
            ("source_mtime", True),
            ("file_type", "unknown"),
        ],
    )
    def test_rejects_invalid_source_values(self, source, field, value):
        raw = json.loads(encode_source(source))
        raw["source"][field] = value
        with pytest.raises(ValueError):
            decode_source(json.dumps(raw))
