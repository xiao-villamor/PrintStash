"""An editing base belongs to one database history, not only an integer counter."""

from enum import Enum

from pydantic import BaseModel, Field


class EditingBase(BaseModel):
    edit_epoch: str = Field(pattern=r"^[0-9a-f]{32}$")
    edit_version: int = Field(strict=True, gt=0)


class EditContract(str, Enum):
    CONDITIONAL_V1 = "conditional-v1"


class EditPrecondition(BaseModel):
    if_match: str | None = None
    contract: str | None = None
