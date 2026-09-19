"""An individual's query consent never modifies another user's settings."""

from sqlmodel import select

from app.db.models import InferenceEndpoint, User, UserSearchPreferences
from app.db.transactions import begin_write
from app.modules.inference.configuration import load
from app.modules.search.configuration import settings
from app.schemas.search_parsing import SearchPreferencesRead


def read(session, user):
    row = session.get(UserSearchPreferences, user.id)
    config = settings(session)
    endpoint = (
        session.get(InferenceEndpoint, config.chat_endpoint_id)
        if config.chat_endpoint_id
        else None
    )
    available = (
        config.enabled
        and config.nl_filters_enabled
        and endpoint is not None
        and endpoint.kind == "chat"
    )
    return SearchPreferencesRead(
        nl_filters_enabled=row.nl_filters_enabled if row else False,
        timezone=row.timezone if row else None,
        effective_timezone=(row.timezone if row else None) or config.timezone,
        available=available,
        endpoint_host=load(endpoint).host if available else None,
    )


def update(session, user, value):
    begin_write(session)
    # Serialize first-time inserts as well as updates for this user on PostgreSQL.
    session.exec(select(User.id).where(User.id == user.id).with_for_update()).one()
    row = session.get(UserSearchPreferences, user.id) or UserSearchPreferences(
        user_id=user.id
    )
    if (
        "nl_filters_enabled" in value.model_fields_set
        and value.nl_filters_enabled is not None
    ):
        row.nl_filters_enabled = value.nl_filters_enabled
    if "timezone" in value.model_fields_set:
        row.timezone = value.timezone
    session.add(row)
    session.commit()
    return read(session, user)
