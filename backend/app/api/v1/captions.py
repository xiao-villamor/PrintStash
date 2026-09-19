"""Authenticated Subject caption reads and EDIT-only human actions."""

from fastapi import APIRouter, Depends, Path
from printstash_core.search.passages import SearchSubject, SubjectType
from sqlmodel import Session

from app.api.v1.search import require_search_user
from app.db.models import User
from app.db.session import get_session
from app.modules.search import captions
from app.schemas.captions import CaptionPatch, CaptionRead

router = APIRouter(prefix="/subjects", tags=["search"])


@router.get("/{subject_type}/{subject_id}/caption", response_model=CaptionRead)
def read_caption(
    subject_type: SubjectType,
    subject_id: int = Path(ge=1),
    user: User = Depends(require_search_user),
    session: Session = Depends(get_session),
):
    return captions.read(session, user, SearchSubject(subject_type, subject_id))


@router.patch("/{subject_type}/{subject_id}/caption", response_model=CaptionRead)
def patch_caption(
    subject_type: SubjectType,
    body: CaptionPatch,
    subject_id: int = Path(ge=1),
    user: User = Depends(require_search_user),
    session: Session = Depends(get_session),
):
    return captions.patch(session, user, SearchSubject(subject_type, subject_id), body)
