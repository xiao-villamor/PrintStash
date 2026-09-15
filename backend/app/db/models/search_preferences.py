"""Personal query-consent and calendar preferences, separate from instance policy."""

from sqlalchemy import Column, ForeignKey, Integer
from sqlmodel import Field

from .base import SQLModel


class UserSearchPreferences(SQLModel, table=True):
    __tablename__ = "user_search_preferences"

    user_id: int = Field(
        sa_column=Column(
            Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
        )
    )
    nl_filters_enabled: bool = False
    timezone: str | None = Field(default=None, max_length=128)
