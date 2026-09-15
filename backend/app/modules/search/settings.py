"""Read persisted search policy without initializing inference providers."""

from sqlmodel import Session

from app.db.models import SystemConfig
from app.schemas.inference import SearchSettings


def settings(session: Session) -> SearchSettings:
    row = session.get(SystemConfig, 1)
    return (
        SearchSettings.model_validate_json(row.ai_search_settings_json)
        if row and row.ai_search_settings_json
        else SearchSettings()
    )
