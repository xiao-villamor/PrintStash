"""Saved project settings are advisory file evidence, separate from print results."""

from pydantic import BaseModel, model_validator

from app.modules.media.three_mf_profile import ProjectProfile
from app.schemas.jobs import DerivativeStatus


class FileProjectProfileRead(BaseModel):
    state: DerivativeStatus
    profile: ProjectProfile | None
    failure_reason: str | None

    @model_validator(mode="after")
    def profile_only_when_ready(self):
        if (self.state is DerivativeStatus.READY) != (self.profile is not None):
            raise ValueError("profile_requires_ready_derivative")
        return self
