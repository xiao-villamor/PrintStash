"""Durable, secret-safe provider connections and browser device pairing."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Protocol, TypeVar
from urllib.parse import urlencode

from sqlalchemy import update
from sqlalchemy.orm.attributes import set_committed_value
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.time import utcnow
from app.db.models import (
    BrowserDevice,
    BrowserPairingCode,
    CaptureProvider,
    ProviderConnection,
    ProviderOAuthState,
    User,
)
from app.services.capture_provider_connections import (
    CultsCredentials,
    CultsMetadataClient,
    MyMiniFactoryCredentials,
    MyMiniFactoryMetadataClient,
    MyMiniFactoryTokens,
    ProviderConnectionError,
    ProviderModelMetadata,
)
from app.services.capture_provider_transport import ProviderTransport

_MAX_DEVICES = 10
_PAIRING_TTL = timedelta(minutes=5)
_PAIRING_MAX_ATTEMPTS = 5
_OAUTH_TTL = timedelta(minutes=10)
_TOKEN_REFRESH_SKEW = timedelta(seconds=60)

_MMFResult = TypeVar("_MMFResult")


class BrowserDeviceNameInUseError(Exception):
    """A live browser device already owns the requested user-scoped name."""


class MyMiniFactoryClient(Protocol):
    async def exchange_code(
        self, credentials: MyMiniFactoryCredentials, *, code: str, redirect_uri: str
    ) -> MyMiniFactoryTokens: ...

    async def refresh_tokens(
        self, credentials: MyMiniFactoryCredentials, tokens: MyMiniFactoryTokens
    ) -> MyMiniFactoryTokens: ...

    async def model_metadata(
        self, model_id: str, tokens: MyMiniFactoryTokens
    ) -> ProviderModelMetadata: ...

    async def file_download_url(
        self, file_id: str, tokens: MyMiniFactoryTokens
    ) -> str: ...


def get_mmf_client() -> MyMiniFactoryClient:
    """Build the bounded official MyMiniFactory client for this call."""
    return MyMiniFactoryMetadataClient(ProviderTransport())


def get_mmf_credentials() -> MyMiniFactoryCredentials:
    """Injection seam; provider application credentials are never user rows."""
    client_id = _secret_value(settings.mmf_client_id)
    client_secret = _secret_value(settings.mmf_client_secret)
    if not client_id or not client_secret:
        raise ProviderConnectionError("provider_not_configured")
    return MyMiniFactoryCredentials(client_id=client_id, client_secret=client_secret)


def _secret_value(value: object) -> str:
    get_secret_value = getattr(value, "get_secret_value", None)
    if callable(get_secret_value):
        value = get_secret_value()
    return value.strip() if isinstance(value, str) else ""


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def credential_matches(value: str, credential_hash: str) -> bool:
    """Constant-time comparison used by the browser-device authentication seam."""
    return hmac.compare_digest(_hash(value), credential_hash)


def _expired(value: datetime) -> bool:
    return (
        value.replace(tzinfo=timezone.utc) <= utcnow()
        if value.tzinfo is None
        else value <= utcnow()
    )


def has_active_provider_connection(
    session: Session, user_id: int, provider: CaptureProvider
) -> bool:
    """Return whether credentials still exist for this user/provider pair."""
    # Lightweight resolver fakes used by callers that do not exercise the DB
    # seam do not expose ``exec``; production Session instances always do.
    if not isinstance(session, Session):  # pragma: no cover - compatibility seam
        return True
    row = session.exec(
        select(ProviderConnection).where(
            ProviderConnection.user_id == user_id,
            ProviderConnection.provider == provider,
        )
    ).first()
    if row is None:
        _invalidate_provider_metadata_cache(user_id, provider)
        return False
    if provider == CaptureProvider.CULTS:
        return bool(row.credential_secret and "\n" in row.credential_secret)
    return bool(row.access_token and row.refresh_token)


def _invalidate_provider_metadata_cache(
    user_id: int, provider: CaptureProvider
) -> None:
    """Invalidate resolver metadata without creating a module import cycle."""
    try:
        from app.services.import_resolvers import invalidate_provider_metadata_cache
    except ImportError:  # pragma: no cover - import-time cycle protection
        return
    invalidate_provider_metadata_cache(user_id, provider.value)


def begin_oauth(session: Session, user_id: int, redirect_uri: str) -> str:
    raw = secrets.token_urlsafe(32)
    session.add(
        ProviderOAuthState(
            user_id=user_id,
            provider=CaptureProvider.MYMINIFACTORY,
            state_hash=_hash(raw),
            redirect_uri=redirect_uri,
            expires_at=utcnow() + _OAUTH_TTL,
        )
    )
    session.flush()
    return raw


def _oauth_reservation_statement(state_hash: str, redirect_uri: str, now: datetime):
    """Build the portable one-time OAuth state reservation."""
    return (
        update(ProviderOAuthState)
        .where(
            col(ProviderOAuthState.state_hash) == state_hash,
            col(ProviderOAuthState.redirect_uri) == redirect_uri,
            col(ProviderOAuthState.used_at).is_(None),
            col(ProviderOAuthState.expires_at) > now,
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )


def consume_oauth(
    session: Session, raw: str, redirect_uri: str
) -> ProviderOAuthState | None:
    now = utcnow()
    reserved = session.exec(
        _oauth_reservation_statement(_hash(raw), redirect_uri, now)
    ).rowcount
    session.flush()
    if reserved != 1:
        return None
    return session.exec(
        select(ProviderOAuthState).where(
            ProviderOAuthState.state_hash == _hash(raw),
            ProviderOAuthState.redirect_uri == redirect_uri,
        )
    ).first()


def authorization_url(state: str, redirect_uri: str) -> str:
    return "https://www.myminifactory.com/users/oauth/authorize?" + urlencode(
        {
            "response_type": "code",
            "client_id": get_mmf_credentials().client_id,
            "state": state,
            "redirect_uri": redirect_uri,
        }
    )


async def finish_oauth(
    session: Session, *, state: str, code: str, redirect_uri: str
) -> bool:
    row = consume_oauth(session, state, redirect_uri)
    if row is None:
        return False
    # Persist the one-time reservation before awaiting the provider.  This
    # keeps the SQLite write lock short and lets a concurrent callback observe
    # the conditional reservation as spent instead of attempting an exchange.
    session.commit()
    try:
        tokens = await get_mmf_client().exchange_code(
            get_mmf_credentials(), code=code, redirect_uri=redirect_uri
        )
    except ProviderConnectionError:
        return False
    connection = session.exec(
        select(ProviderConnection).where(
            ProviderConnection.user_id == row.user_id,
            ProviderConnection.provider == CaptureProvider.MYMINIFACTORY,
        )
    ).first()
    if connection is None:
        connection = ProviderConnection(
            user_id=row.user_id, provider=CaptureProvider.MYMINIFACTORY
        )
        session.add(connection)
    connection.access_token = tokens.access_token
    connection.refresh_token = tokens.refresh_token
    connection.token_expires_at = utcnow() + timedelta(
        seconds=tokens.expires_in_seconds
    )
    connection.updated_at = utcnow()
    session.flush()
    _invalidate_provider_metadata_cache(row.user_id, CaptureProvider.MYMINIFACTORY)
    return True


async def fetch_mmf_model_metadata(
    session: Session, user_id: int, model_id: str
) -> ProviderModelMetadata:
    """Fetch a user's MMF metadata, refreshing only the user's expiring token.

    A successful rotation is committed by the provider service after metadata
    succeeds; failed refreshes/retries roll back the rotation savepoint.
    """
    return await _fetch_mmf_with_fresh_tokens(
        session,
        user_id,
        lambda client, tokens: client.model_metadata(model_id, tokens),
        invalid_connection_code="provider_connection_invalid",
    )


async def fetch_mmf_file_download_url(
    session: Session, user_id: int, file_id: str
) -> str:
    """Resolve a transient signed URL without persisting it on the connection."""
    return await _fetch_mmf_with_fresh_tokens(
        session,
        user_id,
        lambda client, tokens: client.file_download_url(file_id, tokens),
        invalid_connection_code="provider_not_connected",
    )


async def _fetch_mmf_with_fresh_tokens(
    session: Session,
    user_id: int,
    fetch: Callable[[MyMiniFactoryClient, MyMiniFactoryTokens], Awaitable[_MMFResult]],
    *,
    invalid_connection_code: str,
) -> _MMFResult:
    """Fetch through MMF, refreshing an expiring or rejected user token once.

    A token rotation is an isolated provider-service transaction: a successful
    fetch commits only a dedicated session's provider row, while
    refresh/fetch failures roll back that dedicated session.  The caller's
    session (and any unrelated staged work) is never committed by this seam;
    transient signed URLs are never written to the connection row.
    """
    # Do not autoflush unrelated caller work just to inspect token state.  An
    # outer SQLite write transaction would otherwise block the dedicated
    # rotation session from committing its provider row.
    with session.no_autoflush:
        connection = session.exec(
            select(ProviderConnection).where(
                ProviderConnection.user_id == user_id,
                ProviderConnection.provider == CaptureProvider.MYMINIFACTORY,
            )
        ).first()
    if connection is None:
        raise ProviderConnectionError("provider_not_connected")
    if not connection.access_token or not connection.refresh_token:
        raise ProviderConnectionError(invalid_connection_code)

    client = get_mmf_client()
    tokens = MyMiniFactoryTokens(
        connection.access_token,
        connection.refresh_token,
        _expires_in_seconds(connection.token_expires_at),
    )
    rotation_session: Session | None = None
    rotation_connection: ProviderConnection | None = None

    def begin_rotation() -> MyMiniFactoryTokens:
        nonlocal rotation_session, rotation_connection
        if rotation_session is not None and rotation_connection is not None:
            return MyMiniFactoryTokens(
                rotation_connection.access_token or "",
                rotation_connection.refresh_token or "",
                _expires_in_seconds(rotation_connection.token_expires_at),
            )
        # Bind a dedicated ORM session to the same engine.  This is separate
        # from the caller's transaction, so committing a token rotation cannot
        # commit unrelated caller work.
        rotation_session = Session(bind=session.get_bind())
        rotation_connection = rotation_session.exec(
            select(ProviderConnection).where(
                ProviderConnection.user_id == user_id,
                ProviderConnection.provider == CaptureProvider.MYMINIFACTORY,
            )
        ).first()
        if rotation_connection is None:
            rotation_session.close()
            rotation_session = None
            raise ProviderConnectionError("provider_not_connected")
        if (
            not rotation_connection.access_token
            or not rotation_connection.refresh_token
        ):
            rotation_session.close()
            rotation_session = None
            raise ProviderConnectionError(invalid_connection_code)
        return MyMiniFactoryTokens(
            rotation_connection.access_token,
            rotation_connection.refresh_token,
            _expires_in_seconds(rotation_connection.token_expires_at),
        )

    try:
        if _needs_refresh(connection.token_expires_at):
            tokens = begin_rotation()
            assert rotation_session is not None
            assert rotation_connection is not None
            tokens = await _refresh_mmf_connection(
                rotation_session, client, rotation_connection, tokens
            )
        try:
            result = await fetch(client, tokens)
        except ProviderConnectionError as exc:
            if exc.code != "provider_auth_failed":
                raise
            tokens = begin_rotation()
            assert rotation_session is not None
            assert rotation_connection is not None
            tokens = await _refresh_mmf_connection(
                rotation_session, client, rotation_connection, tokens
            )
            result = await fetch(client, tokens)
        if rotation_session is not None:
            # Commit only the dedicated provider session after the request
            # succeeds.  Reflect the durable values in an already-loaded outer
            # object without making that object dirty or committing its work.
            rotation_session.commit()
            assert rotation_connection is not None
            _invalidate_provider_metadata_cache(user_id, CaptureProvider.MYMINIFACTORY)
            for field in (
                "access_token",
                "refresh_token",
                "token_expires_at",
                "updated_at",
            ):
                set_committed_value(
                    connection, field, getattr(rotation_connection, field)
                )
            rotation_session.close()
            rotation_session = None
        return result
    except Exception:
        if rotation_session is not None:
            rotation_session.rollback()
            rotation_session.close()
        raise


async def _refresh_mmf_connection(
    session: Session,
    client: MyMiniFactoryClient,
    connection: ProviderConnection,
    tokens: MyMiniFactoryTokens,
) -> MyMiniFactoryTokens:
    """Persist a refreshed token set together so rotation is never partial."""
    refreshed = await client.refresh_tokens(get_mmf_credentials(), tokens)
    connection.access_token = refreshed.access_token
    connection.refresh_token = refreshed.refresh_token or tokens.refresh_token
    connection.token_expires_at = utcnow() + timedelta(
        seconds=refreshed.expires_in_seconds
    )
    connection.updated_at = utcnow()
    session.flush()
    return MyMiniFactoryTokens(
        connection.access_token,
        connection.refresh_token,
        refreshed.expires_in_seconds,
    )


def _needs_refresh(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return True
    expires = (
        expires_at.replace(tzinfo=timezone.utc)
        if expires_at.tzinfo is None
        else expires_at
    )
    return expires <= utcnow() + _TOKEN_REFRESH_SKEW


def _expires_in_seconds(expires_at: datetime | None) -> int:
    if expires_at is None:
        return 0
    expires = (
        expires_at.replace(tzinfo=timezone.utc)
        if expires_at.tzinfo is None
        else expires_at
    )
    return max(0, int((expires - utcnow()).total_seconds()))


def connect_cults(
    session: Session, user_id: int, username: str, password: str
) -> ProviderConnection:
    row = session.exec(
        select(ProviderConnection).where(
            ProviderConnection.user_id == user_id,
            ProviderConnection.provider == CaptureProvider.CULTS,
        )
    ).first()
    if row is None:
        row = ProviderConnection(user_id=user_id, provider=CaptureProvider.CULTS)
        session.add(row)
    row.credential_secret = f"{username}\n{password}"
    row.updated_at = utcnow()
    session.flush()
    _invalidate_provider_metadata_cache(user_id, CaptureProvider.CULTS)
    return row


async def fetch_cults_model_metadata(
    session: Session, user_id: int, slug: str
) -> ProviderModelMetadata:
    row = session.exec(
        select(ProviderConnection).where(
            ProviderConnection.user_id == user_id,
            ProviderConnection.provider == CaptureProvider.CULTS,
        )
    ).first()
    if row is None or not row.credential_secret or "\n" not in row.credential_secret:
        raise ProviderConnectionError("provider_not_connected")
    username, password = row.credential_secret.split("\n", 1)
    return await CultsMetadataClient(ProviderTransport()).creation_metadata(
        slug, CultsCredentials(username, password)
    )


async def validate_and_connect_cults(
    session: Session, user_id: int, username: str, password: str
) -> ProviderConnection:
    """Validate candidate credentials before replacing the encrypted secret."""
    candidate = CultsCredentials(username, password)
    await CultsMetadataClient(ProviderTransport()).validate_credentials(candidate)
    return connect_cults(session, user_id, username, password)


def disconnect_provider_connection(
    session: Session, user_id: int, provider: CaptureProvider
) -> bool:
    """Delete one connection and invalidate all owner-scoped metadata."""
    row = session.exec(
        select(ProviderConnection).where(
            ProviderConnection.user_id == user_id,
            ProviderConnection.provider == provider,
        )
    ).first()
    if row is None:
        _invalidate_provider_metadata_cache(user_id, provider)
        return False
    session.delete(row)
    _invalidate_provider_metadata_cache(user_id, provider)
    return True


def create_pairing_code(
    session: Session, user_id: int
) -> tuple[str, BrowserPairingCode]:
    raw = secrets.token_urlsafe(24)
    row = BrowserPairingCode(
        user_id=user_id, code_hash=_hash(raw), expires_at=utcnow() + _PAIRING_TTL
    )
    session.add(row)
    session.flush()
    return raw, row


def _pairing_user_lock_statement(code_hash: str):
    """Build the portable per-user write lock used by pairing claims."""
    return (
        update(User)
        .where(
            col(User.id)
            == select(col(BrowserPairingCode.user_id))
            .where(col(BrowserPairingCode.code_hash) == code_hash)
            .scalar_subquery()
        )
        .values(updated_at=col(User.updated_at))
        .execution_options(synchronize_session=False)
    )


def claim_pairing_code(
    session: Session, raw: str, name: str
) -> tuple[str, BrowserDevice] | None:
    """Exchange one valid pairing code for one browser credential.

    Pairing codes are stored only as hashes.  A failed exchange can therefore
    consume an attempt only after the submitted hash identifies a live code;
    unknown values remain indistinguishable from expired/replayed values.
    A per-user database write lock is acquired before the active-device count
    is read, so distinct codes cannot both pass the cap check. The attempt
    increment and successful one-time reservation are conditional database
    updates, so concurrent SQLite/Postgres sessions cannot both spend the same
    code or overwrite each other's failure count.

    The caller owns the transaction. When this returns ``None`` for a live code
    at the device cap, it must commit the intentional attempt increment rather
    than rolling it back with unrelated invalid-code failures.
    """
    now = utcnow()
    code_hash = _hash(raw)
    # Serialize all claims for one owner at the database boundary.  The
    # correlated UPDATE acquires a row write lock before any snapshot/count
    # read: PostgreSQL honors the row lock and SQLite serializes the write
    # transaction (with its configured busy timeout).  Re-reading the code
    # after the lock avoids a stale pre-lock snapshot on SQLite.
    lock_user = session.exec(_pairing_user_lock_statement(code_hash))
    session.flush()
    if lock_user.rowcount != 1:
        return None
    row = session.exec(
        select(BrowserPairingCode)
        .where(BrowserPairingCode.code_hash == code_hash)
        .with_for_update()
    ).first()
    if (
        row is None
        or row.used_at is not None
        or _expired(row.expires_at)
        or row.attempts >= _PAIRING_MAX_ATTEMPTS
    ):
        return None
    existing = session.exec(
        select(BrowserDevice).where(
            BrowserDevice.user_id == row.user_id,
            BrowserDevice.name == name,
            BrowserDevice.revoked_at.is_(None),  # type: ignore[union-attr]
        )
    ).first()
    if existing is not None:
        raise BrowserDeviceNameInUseError
    active = session.exec(
        select(BrowserDevice).where(
            BrowserDevice.user_id == row.user_id,
            BrowserDevice.revoked_at.is_(None),  # type: ignore[union-attr]
        )
    ).all()
    if len(active) >= _MAX_DEVICES:
        # Do not use ``row.attempts += 1`` here: two SQLite connections (where
        # FOR UPDATE is ignored) would both read the same value and lose an
        # attempt.  The compare-and-increment is atomic on both supported DBs.
        incremented = session.exec(
            update(BrowserPairingCode)
            .where(
                col(BrowserPairingCode.id) == row.id,
                col(BrowserPairingCode.used_at).is_(None),
                col(BrowserPairingCode.expires_at) > now,
                col(BrowserPairingCode.attempts) < _PAIRING_MAX_ATTEMPTS,
            )
            .values(attempts=col(BrowserPairingCode.attempts) + 1)
            .execution_options(synchronize_session=False)
        ).rowcount
        session.flush()
        if incremented == 1:
            session.expire(row, ["attempts"])
        return None

    # Reserve the code before creating the device.  This conditional UPDATE is
    # the SQLite-safe equivalent of the PostgreSQL row lock above and makes a
    # pair of concurrent exchanges produce at most one credential.
    reserved = session.exec(
        update(BrowserPairingCode)
        .where(
            col(BrowserPairingCode.id) == row.id,
            col(BrowserPairingCode.used_at).is_(None),
            col(BrowserPairingCode.expires_at) > now,
            col(BrowserPairingCode.attempts) < _PAIRING_MAX_ATTEMPTS,
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    ).rowcount
    if reserved != 1:
        return None
    session.flush()
    session.expire(row, ["used_at"])
    credential = secrets.token_urlsafe(48)
    device = session.exec(
        select(BrowserDevice).where(
            BrowserDevice.user_id == row.user_id,
            BrowserDevice.name == name,
        )
    ).first()
    if device is None:
        device = BrowserDevice(
            user_id=row.user_id, name=name, credential_hash=_hash(credential)
        )
        session.add(device)
    else:
        # Reuse the revoked row so the global user/name constraint remains
        # valid while rotating the credential that was previously revoked.
        device.credential_hash = _hash(credential)
        device.revoked_at = None
    session.flush()
    return credential, device
