from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlmodel import Session, select

from app.core.ratelimit import rate_limit
from app.core.security import require_auth, require_user
from app.core.time import utcnow
from app.db.models import BrowserDevice, CaptureProvider, ProviderConnection, User
from app.db.session import get_session
from app.schemas.provider_connections import (
    BrowserDevicePatch,
    BrowserDeviceRead,
    BrowserPairingClaim,
    BrowserPairingClaimRead,
    BrowserPairingCreateRead,
    CultsConnectRequest,
    OAuthAuthorizeRead,
    ProviderConnectionRead,
)
from app.services import provider_connections as service

router = APIRouter(prefix="/provider-connections", tags=["provider-connections"])
pairing_router = APIRouter(prefix="/browser-pairings", tags=["browser-pairings"])
_claim_limit = rate_limit(10, 60.0)


def _device(row: BrowserDevice) -> BrowserDeviceRead:
    assert row.id is not None
    return BrowserDeviceRead(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


@router.get("", response_model=list[ProviderConnectionRead])
def list_connections(
    current_user: User = Depends(require_user), session: Session = Depends(get_session)
) -> list[ProviderConnectionRead]:
    rows = {
        row.provider: row
        for row in session.exec(
            select(ProviderConnection).where(
                ProviderConnection.user_id == current_user.id
            )
        ).all()
    }
    return [
        ProviderConnectionRead(
            provider=provider.value,
            connected=provider in rows,
            updated_at=rows[provider].updated_at if provider in rows else None,
        )
        for provider in CaptureProvider
    ]


@router.post(
    "/cults/connect",
    response_model=ProviderConnectionRead,
    dependencies=[Depends(require_auth)],
)
async def connect_cults(
    body: CultsConnectRequest,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> ProviderConnectionRead:
    assert current_user.id is not None
    try:
        row = await service.validate_and_connect_cults(
            session, current_user.id, body.username, body.password
        )
    except service.ProviderConnectionError as exc:
        raise HTTPException(
            status_code=400, detail="provider_connection_validation_failed"
        ) from exc
    session.commit()
    return ProviderConnectionRead(
        provider="cults", connected=True, updated_at=row.updated_at
    )


@router.post(
    "/myminifactory/authorize",
    response_model=OAuthAuthorizeRead,
    dependencies=[Depends(require_auth)],
)
def authorize_myminifactory(
    request: Request,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> OAuthAuthorizeRead:
    assert current_user.id is not None
    try:
        service.get_mmf_credentials()
    except service.ProviderConnectionError as exc:
        raise HTTPException(status_code=503, detail=exc.code) from None
    redirect_uri = str(request.url_for("myminifactory_callback"))
    state = service.begin_oauth(session, current_user.id, redirect_uri)
    session.commit()
    return OAuthAuthorizeRead(
        authorization_url=service.authorization_url(state, redirect_uri)
    )


@router.get("/myminifactory/callback", name="myminifactory_callback")
async def myminifactory_callback(
    request: Request,
    state: str | None = None,
    code: str | None = None,
    session: Session = Depends(get_session),
) -> dict[str, str]:
    if not state or not code:
        raise HTTPException(status_code=400, detail="invalid_oauth_callback")
    redirect_uri = str(request.url_for("myminifactory_callback"))
    if not await service.finish_oauth(
        session, state=state, code=code, redirect_uri=redirect_uri
    ):
        # A valid state is consumed before exchange; commit that transition even
        # when the provider rejects the code so callbacks cannot be replayed.
        session.commit()
        raise HTTPException(status_code=400, detail="invalid_oauth_callback")
    session.commit()
    return {"status": "connected"}


@router.delete(
    "/{provider}/disconnect",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_auth)],
)
def disconnect(
    provider: CaptureProvider,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> None:
    assert current_user.id is not None
    if service.disconnect_provider_connection(session, current_user.id, provider):
        session.commit()


@pairing_router.post(
    "",
    response_model=BrowserPairingCreateRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_auth)],
)
def create_pairing(
    current_user: User = Depends(require_user), session: Session = Depends(get_session)
) -> BrowserPairingCreateRead:
    assert current_user.id is not None
    code, row = service.create_pairing_code(session, current_user.id)
    session.commit()
    return BrowserPairingCreateRead(code=code, expires_at=row.expires_at)


@pairing_router.post(
    "/claim",
    response_model=BrowserPairingClaimRead,
    dependencies=[Depends(_claim_limit)],
)
def claim_pairing(
    body: BrowserPairingClaim, session: Session = Depends(get_session)
) -> BrowserPairingClaimRead:
    try:
        claimed = service.claim_pairing_code(session, body.code, body.name)
    except service.BrowserDeviceNameInUseError as exc:
        # The per-user lock is intentionally held through the name check. Roll
        # back its no-op write before returning so the untouched code remains
        # available for a retry with a different name.
        session.rollback()
        raise HTTPException(
            status_code=409, detail="browser_device_name_in_use"
        ) from exc
    if claimed is None:
        # A live code rejected at the device cap spends an attempt. Commit the
        # service-owned conditional increment; unknown, expired, replayed, and
        # locked codes performed no writes, so committing those reads is safe.
        session.commit()
        raise HTTPException(status_code=400, detail="invalid_or_expired_pairing_code")
    credential, device = claimed
    session.commit()
    return BrowserPairingClaimRead(credential=credential, device=_device(device))


@pairing_router.get("", response_model=list[BrowserDeviceRead])
def list_devices(
    current_user: User = Depends(require_user), session: Session = Depends(get_session)
) -> list[BrowserDeviceRead]:
    return [
        _device(row)
        for row in session.exec(
            select(BrowserDevice)
            .where(BrowserDevice.user_id == current_user.id)
            .order_by(BrowserDevice.id)  # type: ignore[arg-type]
        ).all()
    ]


@pairing_router.patch(
    "/{device_id}",
    response_model=BrowserDeviceRead,
    dependencies=[Depends(require_auth)],
)
def rename_device(
    device_id: int,
    body: BrowserDevicePatch,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> BrowserDeviceRead:
    row = session.exec(
        select(BrowserDevice).where(
            BrowserDevice.id == device_id, BrowserDevice.user_id == current_user.id
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="browser_device_not_found")
    row.name = body.name
    session.commit()
    return _device(row)


@pairing_router.delete(
    "/{device_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_auth)],
)
def revoke_device(
    device_id: int,
    current_user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> None:
    row = session.exec(
        select(BrowserDevice).where(
            BrowserDevice.id == device_id, BrowserDevice.user_id == current_user.id
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="browser_device_not_found")
    row.revoked_at = utcnow()
    session.commit()
