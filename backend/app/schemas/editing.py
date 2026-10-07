"""An editing base belongs to one database history, not only an integer counter."""

from pydantic import BaseModel, Field


class EditingBase(BaseModel):
    edit_epoch: str = Field(pattern=r"^[0-9a-f]{32}$")
    edit_version: int = Field(strict=True, gt=0)
