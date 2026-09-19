"""Separate machine text, with explicit human revision and dismissal actions."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CaptionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["edit", "dismiss", "reset", "generate"]
    text: str | None = Field(default=None, max_length=2048)
    version_token: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    @model_validator(mode="after")
    def validate_text(self):
        if (self.action == "edit") != (self.text is not None):
            raise ValueError("caption_text_required_for_edit")
        if self.text is not None and not self.text.strip():
            raise ValueError("caption_text_empty")
        return self


class CaptionRead(BaseModel):
    state: Literal["generated", "edited", "dismissed"] | None = None
    phase: Literal["pending", "running", "ready", "failed"] | None = None
    text: str = ""
    version_token: str | None = None
    can_edit: bool
    can_generate: bool
    unavailable_reason: str | None = None
    model: str | None = None
    model_revision: str | None = None
    recipe: str | None = None
    edited_by: int | None = None
    updated_at: datetime | None = None
    error_code: str | None = None
