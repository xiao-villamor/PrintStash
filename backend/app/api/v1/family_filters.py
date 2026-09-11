"""Shared structured Model filters for Family-aware library browsing."""

from datetime import datetime
from typing import List, Literal, Optional

from fastapi import Depends, HTTPException, Query

from app.core.security import require_user
from app.db.models import FileRevisionStatus, FileType, PrintJobState, User
from app.schemas.family_types import VariantRole
from app.schemas.models import ModelFilters


def family_browse_filters(
    collection: Optional[str] = Query(None, max_length=512),
    direct: bool = Query(False),
    tag: Optional[List[str]] = Query(None, max_length=64),
    q: Optional[str] = Query(None, max_length=255),
    printer_id: Optional[int] = Query(None, gt=0),
    printer_presence: Optional[Literal["any", "none"]] = Query(None),
    favorites: bool = Query(False),
    file_type: Optional[List[FileType]] = Query(None),
    material_type: Optional[List[str]] = Query(None, max_length=64),
    slicer_name: Optional[List[str]] = Query(None, max_length=64),
    printer_model: Optional[List[str]] = Query(None, max_length=64),
    revision_status: Optional[List[FileRevisionStatus]] = Query(None),
    printed: Optional[bool] = Query(None),
    print_outcome: Optional[List[PrintJobState]] = Query(None),
    storage_filter: Optional[List[Literal["vault", "external"]]] = Query(
        None, alias="storage"
    ),
    uploaded_after: Optional[datetime] = Query(None),
    uploaded_before: Optional[datetime] = Query(None),
    has_similar_candidates: Optional[bool] = Query(None),
    family_id: int | None = Query(None, gt=0),
    family_role: VariantRole | None = Query(None),
    in_family: bool | None = Query(None),
    current_user: User = Depends(require_user),
) -> ModelFilters:
    if (
        printer_id is not None or printer_presence is not None
    ) and not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="admin_required")
    filters = ModelFilters(
        collection=collection,
        direct=direct,
        tag=tag or [],
        q=q,
        printer_id=printer_id,
        printer_presence=printer_presence,
        favorites=favorites,
        file_type=file_type or [],
        material_type=material_type or [],
        slicer_name=slicer_name or [],
        printer_model=printer_model or [],
        revision_status=revision_status or [],
        printed=printed,
        print_outcome=print_outcome or [],
        storage=storage_filter or [],
        uploaded_after=uploaded_after,
        uploaded_before=uploaded_before,
        has_similar_candidates=has_similar_candidates,
        family_id=family_id,
        family_role=family_role,
        in_family=in_family,
    )
    return filters
