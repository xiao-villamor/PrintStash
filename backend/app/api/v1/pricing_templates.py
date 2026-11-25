"""Vault-wide reusable margin and tax settings."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.errors import ErrorKind, OperationError
from app.core.security import require_auth, require_superuser, require_user
from app.db.models import PricingTemplate
from app.db.session import get_session
from app.schemas.production_costing import PricingTemplateCreate, PricingTemplateRead

router = APIRouter(prefix="/pricing-templates", tags=["pricing-templates"])


@router.get("", dependencies=[Depends(require_user)])
def list_templates(
    offset: int = Query(0, ge=0),
    limit: int = Query(51, ge=1, le=100),
    session: Session = Depends(get_session),
) -> list[PricingTemplateRead]:
    templates = session.exec(
        select(PricingTemplate)
        .order_by(col(PricingTemplate.name))
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        PricingTemplateRead.model_validate(row, from_attributes=True)
        for row in templates
    ]


@router.post(
    "",
    status_code=201,
    dependencies=[Depends(require_superuser), Depends(require_auth)],
)
def create_template(
    payload: PricingTemplateCreate, session: Session = Depends(get_session)
) -> PricingTemplateRead:
    template = PricingTemplate(**payload.model_dump())
    session.add(template)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        if (
            session.exec(
                select(PricingTemplate.id).where(PricingTemplate.name == payload.name)
            ).first()
            is None
        ):
            raise
        raise OperationError(
            "pricing_template_name_exists", kind=ErrorKind.CONFLICT
        ) from exc
    return PricingTemplateRead.model_validate(template, from_attributes=True)


@router.delete(
    "/{template_id}",
    status_code=204,
    dependencies=[Depends(require_superuser), Depends(require_auth)],
)
def delete_template(template_id: str, session: Session = Depends(get_session)) -> None:
    template = session.get(PricingTemplate, template_id)
    if template is not None:
        session.delete(template)
        session.commit()
