"""Administrator control over how many native mesh jobs run at once."""

from fastapi import APIRouter, Depends, Response
from sqlmodel import Session

from app.api.edit_preconditions import edit_precondition
from app.core.security import require_superuser
from app.db.models import User
from app.db.session import get_session
from app.modules.administration import config_edits, mesh_workers_config
from app.modules.administration.mesh_workers_config import (
    MeshWorkersRead,
    MeshWorkersUpdate,
)
from app.schemas.editing import EditPrecondition

router = APIRouter(
    prefix="/config/mesh-workers",
    tags=["config"],
    dependencies=[Depends(require_superuser)],
)


@router.get("", response_model=MeshWorkersRead)
def read_mesh_workers(response: Response, session: Session = Depends(get_session)):
    result = mesh_workers_config.read_settings(session)
    response.headers["ETag"] = config_edits.etag(result)
    return result


@router.put("", response_model=MeshWorkersRead)
def update_mesh_workers(
    body: MeshWorkersUpdate,
    response: Response,
    session: Session = Depends(get_session),
    actor: User = Depends(require_superuser),
    precondition: EditPrecondition = Depends(edit_precondition),
):
    result = mesh_workers_config.update_settings(
        session, body, actor=actor, base=config_edits.expected_base(precondition)
    )
    response.headers["ETag"] = config_edits.etag(result)
    return result


@router.delete("", response_model=MeshWorkersRead)
def reset_mesh_workers(
    response: Response,
    session: Session = Depends(get_session),
    actor: User = Depends(require_superuser),
    precondition: EditPrecondition = Depends(edit_precondition),
):
    result = mesh_workers_config.update_settings(
        session, None, actor=actor, base=config_edits.expected_base(precondition)
    )
    response.headers["ETag"] = config_edits.etag(result)
    return result
