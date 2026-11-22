"""Project profile responses cannot imply evidence exists before analysis is ready."""

import pytest
from pydantic import ValidationError

from app.modules.media.three_mf_profile import ProjectProfile
from app.schemas.jobs import DerivativeStatus
from app.schemas.project_profiles import FileProjectProfileRead


@pytest.fixture
def saved_project():
    return ProjectProfile(
        name="Draft",
        printer="Example",
        materials=("PLA",),
        nozzle_diameters_mm=(0.4,),
        layer_height_mm=0.2,
        wall_loops=2,
        infill_percent=10,
        plates=(),
    )


class TestFileProjectProfileRead:
    def test_preserves_explicit_ready_project_evidence(self, saved_project):
        result = FileProjectProfileRead(
            state=DerivativeStatus.READY, profile=saved_project, failure_reason=None
        )

        assert result.profile == saved_project
        assert result.state is DerivativeStatus.READY

    def test_rejects_ready_profile_state_without_evidence(self):
        with pytest.raises(ValidationError, match="profile_requires_ready_derivative"):
            FileProjectProfileRead(
                state=DerivativeStatus.READY, profile=None, failure_reason=None
            )

    @pytest.mark.parametrize(
        "state",
        [state for state in DerivativeStatus if state is not DerivativeStatus.READY],
    )
    def test_rejects_profile_evidence_before_ready(self, state, saved_project):
        with pytest.raises(ValidationError, match="profile_requires_ready_derivative"):
            FileProjectProfileRead(
                state=state, profile=saved_project, failure_reason=None
            )
