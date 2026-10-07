"""Administrator controls for disposable verified Artifact materializations."""

from fastapi import APIRouter, Depends, Response
from sqlmodel import Session

from app.api.edit_preconditions import edit_precondition
from app.core.security import require_superuser
from app.db.models import User
from app.db.session import get_session
from app.modules.administration import artifact_cache_config, config_edits
from app.modules.administration.artifact_cache_config import (
    CacheSettings,
    CacheSettingsRead,
)
from app.schemas.editing import EditPrecondition

router = APIRouter(
    prefix="/config/artifact-cache",
    tags=["config"],
    dependencies=[Depends(require_superuser)],
)


@router.get("", response_model=CacheSettingsRead)
def read_cache(response: Response, session: Session = Depends(get_session)):
    result = artifact_cache_config.read_settings(session)
    response.headers["ETag"] = config_edits.etag(result)
    return result


@router.put("", response_model=CacheSettingsRead)
def update_cache(
    body: CacheSettings,
    response: Response,
    session: Session = Depends(get_session),
    actor: User = Depends(require_superuser),
    precondition: EditPrecondition = Depends(edit_precondition),
):
    result = artifact_cache_config.update_settings(
        session, body, actor=actor, base=config_edits.expected_base(precondition)
    )
    response.headers["ETag"] = config_edits.etag(result)
    return result


@router.delete("", response_model=CacheSettingsRead)
def reset_cache(
    response: Response,
    session: Session = Depends(get_session),
    actor: User = Depends(require_superuser),
    precondition: EditPrecondition = Depends(edit_precondition),
):
    result = artifact_cache_config.update_settings(
        session, None, actor=actor, base=config_edits.expected_base(precondition)
    )
    response.headers["ETag"] = config_edits.etag(result)
    return result


@router.post("/clear", response_model=CacheSettingsRead)
def clear_cache(session: Session = Depends(get_session)):
    return artifact_cache_config.clear_cache(session)
