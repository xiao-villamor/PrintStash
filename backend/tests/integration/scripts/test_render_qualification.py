"""A qualification run cannot change corpus or software after freezing inputs."""

from copy import deepcopy

import pytest

from scripts import render_qualification as qualification
from scripts.gpu_render_measurement import Flow


@pytest.fixture
def manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(qualification, "working_tree_dirty", lambda: False)
    path = tmp_path / "control.stl"
    path.write_bytes(b"frozen corpus control")
    return path, qualification.freeze([path], "small-stl", Flow.PREVIEW)


class TestFreeze:
    def test_binds_source_digest(self, manifest):
        source, frozen = manifest
        qualification.verify(frozen, source, Flow.PREVIEW)
        assert frozen["sources"] == [
            {"path": str(source), "sha256": qualification.fingerprint(source)}
        ]
        assert frozen["minimum_observations"] == 30
        assert frozen["minimum_complete_flow_speedup"] == 1.5

    def test_refuses_dirty_qualification_source(self, manifest, monkeypatch):
        source, _ = manifest
        monkeypatch.setattr(qualification, "working_tree_dirty", lambda: True)
        with pytest.raises(ValueError, match="qualification_requires_committed_source"):
            qualification.freeze([source], "small-stl", Flow.PREVIEW)

    def test_refuses_source_mutation(self, manifest):
        source, frozen = manifest
        source.write_bytes(b"changed corpus")
        with pytest.raises(ValueError, match="qualification_source_changed"):
            qualification.verify(frozen, source, Flow.PREVIEW)

    def test_refuses_quality_tolerance_mutation(self, manifest):
        source, frozen = manifest
        changed = deepcopy(frozen)
        changed["quality_policy"]["rgba_max_difference"] = 9
        with pytest.raises(ValueError, match="qualification_policy_changed"):
            qualification.verify(changed, source, Flow.PREVIEW)

    def test_refuses_software_mutation(self, manifest):
        source, frozen = manifest
        frozen["versions"]["wgpu"] = "obsolete"
        with pytest.raises(ValueError, match="qualification_software_changed"):
            qualification.verify(frozen, source, Flow.PREVIEW)

    def test_refuses_workload_flow_mutation(self, manifest):
        source, frozen = manifest
        with pytest.raises(ValueError, match="qualification_policy_changed"):
            qualification.verify(frozen, source, Flow.MULTIVIEW)
