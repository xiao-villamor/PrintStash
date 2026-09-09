"""Administrative controls for verified Vault migration."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session

from app.core.security import require_superuser
from app.db.models import User
from app.db.session import get_session, get_session_factory
from app.modules.storage.vault_migration import VaultMigrations
from app.schemas.vault_migration import MigrationPolicy, MigrationRunRead

router = APIRouter(prefix="/storage/migrations", tags=["storage"])


class PreflightRequest(BaseModel):
    destination: dict[str, object]
    backup_id: str
    backup_source_ref: str | None = None
    policy: MigrationPolicy = MigrationPolicy()


class PlanRequest(BaseModel):
    plan_digest: str


class CleanupRequest(BaseModel):
    confirmation: str
    source: bool = True
    backup_id: str | None = None
    backup_source_ref: str | None = None


def owner() -> VaultMigrations:
    return VaultMigrations(get_session_factory())


def invoke(action):
    try:
        return action()
    except ValueError as exc:
        code = str(exc)
        if not code.startswith("migration_") or not code.replace("_", "").isalnum():
            code = "migration_invalid_configuration"
        raise HTTPException(409, detail=code) from exc
    except TimeoutError as exc:
        raise HTTPException(409, detail="migration_readers_busy") from exc


@router.post("/preflight", response_model=MigrationRunRead)
def preflight(body: PreflightRequest, user: User = Depends(require_superuser)):
    return invoke(
        lambda: owner().preflight(
            body.destination,
            backup_id=body.backup_id,
            backup_source_ref=body.backup_source_ref,
            actor_id=user.id,
            policy=body.policy.model_dump(),
        )
    )


@router.get("", response_model=list[MigrationRunRead])
def list_runs(_user: User = Depends(require_superuser)):
    return invoke(lambda: owner().list())


@router.get("/{run_id}", response_model=MigrationRunRead)
def status(run_id: str, _user: User = Depends(require_superuser)):
    return invoke(lambda: owner().get(run_id))


@router.post("/{run_id}/start", response_model=MigrationRunRead)
def start(run_id: str, body: PlanRequest, _user: User = Depends(require_superuser)):
    return invoke(lambda: owner().start(run_id, body.plan_digest))


@router.post("/{run_id}/advance", response_model=MigrationRunRead)
def advance(run_id: str, _user: User = Depends(require_superuser)):
    return invoke(lambda: owner().advance(run_id))


@router.post("/{run_id}/cutover", response_model=MigrationRunRead)
def cutover(
    run_id: str,
    _user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
):
    session.close()
    return invoke(lambda: owner().cutover(run_id))


@router.post("/{run_id}/recover", response_model=MigrationRunRead)
def recover(
    run_id: str,
    _user: User = Depends(require_superuser),
    session: Session = Depends(get_session),
):
    session.close()
    return invoke(lambda: owner().recover(run_id))


@router.post("/{run_id}/cleanup", response_model=MigrationRunRead)
def cleanup(
    run_id: str, body: CleanupRequest, _user: User = Depends(require_superuser)
):
    return invoke(
        lambda: owner().cleanup(
            run_id,
            confirmation=body.confirmation,
            source=body.source,
            backup_id=body.backup_id,
            backup_source_ref=body.backup_source_ref,
        )
    )


@router.post("/{run_id}/pause", response_model=MigrationRunRead)
def pause(run_id: str, _user: User = Depends(require_superuser)):
    return invoke(lambda: owner().pause(run_id))


@router.post("/{run_id}/resume", response_model=MigrationRunRead)
def resume(run_id: str, _user: User = Depends(require_superuser)):
    return invoke(lambda: owner().resume(run_id))


@router.post("/{run_id}/retry", response_model=MigrationRunRead)
def retry(run_id: str, _user: User = Depends(require_superuser)):
    return invoke(lambda: owner().resume(run_id, failed_only=True))


@router.post("/{run_id}/full-audit", response_model=MigrationRunRead)
def full_audit(run_id: str, _user: User = Depends(require_superuser)):
    return invoke(lambda: owner().full_audit(run_id))


class RetainRequest(BaseModel):
    remove_credentials: bool = False


@router.post("/{run_id}/retain", response_model=MigrationRunRead)
def retain(run_id: str, body: RetainRequest, _user: User = Depends(require_superuser)):
    return invoke(
        lambda: owner().retain(run_id, remove_credentials=body.remove_credentials)
    )


@router.get("/{run_id}/report", response_model=MigrationRunRead)
def report(run_id: str, _user: User = Depends(require_superuser)):
    return invoke(lambda: owner().get(run_id))
