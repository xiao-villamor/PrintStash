"""Public metadata for storage-provider selection."""

import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

import app.modules.backups.backup.targets as backup_targets
from app.core.security import require_superuser, require_user
from app.core.time import utcnow
from app.db.models import StorageFailureDomainDeclaration, User
from app.db.session import get_session, get_session_factory
from app.modules.storage.capacity import CapacityManager
from app.modules.storage.storage_identity import identity_evidence, provider_domain
from app.modules.storage.storage_inventory import InventoryReport, StorageInventory
from app.modules.storage.storage_providers import StorageProvider, provider_catalogue

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
    from app.modules.storage.storage_backend.runtime import get_backend

    result = [("vault", "Vault", get_backend().storage_target)]
    result.extend(backup_targets.configured_backup_storage_targets())
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
    if provider_domain(target):
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


@router.get("/inventory", response_model=InventoryReport)
def storage_inventory(
    session: Session = Depends(get_session),
    actor: User = Depends(require_superuser),
) -> InventoryReport:
    from app.modules.storage.storage_inventory import inventory_report

    return inventory_report(session, CapacityManager(get_session_factory()))


@router.post("/inventory/sample", response_model=StorageInventory)
def sample_storage_inventory(
    session: Session = Depends(get_session),
    actor: User = Depends(require_superuser),
):
    from app.modules.storage.storage_inventory import inventory, record_sample

    current = inventory(session, refresh_provider=True)
    record_sample(session, current)
    return current


@router.get("/inventory/models")
def storage_logical_models(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    collection_id: int | None = Query(default=None, ge=0),
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
):
    from app.modules.storage.storage_inventory import logical_drilldown

    return logical_drilldown(
        session, actor, offset=offset, limit=limit, collection_id=collection_id
    )


@router.post("/inventory/cleanup-staging")
def cleanup_storage_staging(
    session: Session = Depends(get_session),
    actor: User = Depends(require_superuser),
):
    from app.modules.storage.storage_inventory import cleanup_expired_staging

    return cleanup_expired_staging(session, actor)


@router.post("/inventory/cleanup-cache")
def cleanup_storage_derived_cache(
    session: Session = Depends(get_session),
    actor: User = Depends(require_superuser),
):
    from app.modules.storage.storage_inventory import cleanup_derived_cache

    return cleanup_derived_cache(session, actor)


@router.get("/inventory/collections")
def storage_logical_collections(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_session),
    actor: User = Depends(require_user),
):
    from app.modules.storage.storage_inventory import collection_drilldown

    return collection_drilldown(session, actor, offset=offset, limit=limit)


@router.get("/inventory/activity", dependencies=[Depends(require_superuser)])
def storage_capacity_activity(session: Session = Depends(get_session)):
    from app.modules.storage.storage_inventory import capacity_activity

    return capacity_activity(session)


@router.get(
    "/inventory/cleanup-opportunities", dependencies=[Depends(require_superuser)]
)
def storage_cleanup_opportunities(session: Session = Depends(get_session)):
    from app.modules.storage.storage_inventory import cleanup_opportunities

    return cleanup_opportunities(session)
