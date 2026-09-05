"""Public metadata for storage-provider selection."""

import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

from app.core.security import require_superuser
from app.core.time import utcnow
from app.db.models import StorageFailureDomainDeclaration, User
from app.db.session import get_session
from app.services.storage_identity import identity_evidence
from app.services.storage_providers import StorageProvider, provider_catalogue

router = APIRouter(prefix="/storage", tags=["storage"])


@router.get(
    "/providers",
    response_model=list[StorageProvider],
    summary="List storage providers",
)
def list_storage_providers() -> list[StorageProvider]:
    """Return metadata only; credentials and configured values are never exposed."""
    return provider_catalogue()


def _configured_targets():
    from app.services import backup
    from app.services.backup_destination import configured_destinations
    from app.services.storage_backend import get_backend

    result = [("vault", "Vault", get_backend().storage_target)]
    legacy = backup._get_backup_s3_target()
    if legacy is not None:
        result.append(("backup", "S3 backup", legacy.storage_target))
    for destination in configured_destinations():
        result.append(("backup", destination.name, destination.backend.storage_target))
    return [(role, name, target) for role, name, target in result if target is not None]


@router.get("/targets", dependencies=[Depends(require_superuser)])
def list_storage_targets() -> list[dict]:
    """Administrator view of credential-free identity and independence evidence."""
    return [
        {
            "role": role,
            "name": name,
            "target_ref": target.target_ref,
            "identity": target.model_dump(),
            "evidence": identity_evidence(target),
        }
        for role, name, target in _configured_targets()
    ]


class FailureDomainDeclarationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failure_domain: str = Field(
        min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9._-]*$"
    )


@router.put("/targets/{target_ref}/failure-domain")
def declare_failure_domain(
    target_ref: str,
    body: FailureDomainDeclarationInput,
    session: Session = Depends(get_session),
    actor: User = Depends(require_superuser),
) -> dict:
    target = next(
        (
            target
            for _, _, target in _configured_targets()
            if target.target_ref == target_ref
        ),
        None,
    )
    if target is None:
        raise HTTPException(409, detail="storage_target_changed")
    if target.provider_domain:
        raise HTTPException(409, detail="storage_failure_domain_provider_defined")
    declaration = session.get(StorageFailureDomainDeclaration, target_ref)
    if declaration is None:
        declaration = StorageFailureDomainDeclaration(
            target_ref=target_ref,
            target_identity=json.dumps(target.model_dump(), sort_keys=True),
            failure_domain=body.failure_domain,
            revision=uuid4().hex,
            declared_by=actor.id,
        )
    else:
        declaration.failure_domain = body.failure_domain
        declaration.revision = uuid4().hex
        declaration.declared_by = actor.id
        declaration.updated_at = utcnow()
    session.add(declaration)
    session.commit()
    return {
        "target_ref": target_ref,
        "failure_domain": declaration.failure_domain,
        "revision": declaration.revision,
    }


@router.delete(
    "/targets/{target_ref}/failure-domain", dependencies=[Depends(require_superuser)]
)
def remove_failure_domain(
    target_ref: str, session: Session = Depends(get_session)
) -> dict:
    declaration = session.get(StorageFailureDomainDeclaration, target_ref)
    if declaration is not None:
        session.delete(declaration)
        session.commit()
    return {"target_ref": target_ref, "declared": False}
