from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from sqlmodel import Session, select

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import ApiKey, RefreshToken, User
from app.core.time import utcnow

logger = get_logger(__name__)
ACCESS_BLOCKLIST: set[str] = set()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def create_access_token(
    user_id: int, username: str, scope: str, expires_delta: timedelta | None = None,) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.access_token_expire_minutes)
    )
    token_id = secrets.token_hex(16)
    payload = {
        "sub": str(user_id),
        "username": username,
        "scope": scope,
        "jti": token_id,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
        token_id = payload.get("jti")
        if isinstance(token_id, str) and token_id in ACCESS_BLOCKLIST:
            return None
        return payload
    except JWTError as exc:
        logger.debug("jwt verification failed: %s", exc)
        return None


def create_file_download_token(file_id: int) -> str:
    """Mint a short-lived, file-scoped token for "Open in slicer" downloads.

    An external slicer process opens the download URL with no Authorization
    header, so it can't use a normal access token. This token is a bearer
    capability for *one* file, narrowly scoped and short-lived.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.slicer_download_token_expire_minutes
    )
    payload = {
        "scope": "file_download",
        "file_id": int(file_id),
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def verify_file_download_token(token: str, file_id: int) -> bool:
    """True iff *token* is a valid, unexpired download token for *file_id*."""
    payload = verify_access_token(token)
    if not payload:
        return False
    if payload.get("scope") != "file_download":
        return False
    return payload.get("file_id") == int(file_id)


def revoke_access_token(token: str) -> None:
    payload = verify_access_token(token)
    if not payload:
        return
    token_id = payload.get("jti")
    if isinstance(token_id, str):
        ACCESS_BLOCKLIST.add(token_id)


def create_refresh_token(
    session: Session, user_id: int, minutes: int = 60 * 24 * 14
) -> str:
    raw_token = secrets.token_urlsafe(48)
    expires_at = utcnow() + timedelta(minutes=minutes)
    record = RefreshToken(
        user_id=user_id,
        token_hash=_token_hash(raw_token),
        expires_at=expires_at,
        revoked=False,
    )
    session.add(record)
    session.commit()
    return raw_token


def rotate_refresh_token(session: Session, raw_token: str) -> Optional[RefreshToken]:
    token_hash = _token_hash(raw_token)
    record = session.exec(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    ).first()
    if record is None or record.revoked or _as_utc(record.expires_at) <= utcnow():
        return None
    record.revoked = True
    record.revoked_at = utcnow()
    session.add(record)
    session.commit()
    return record


def revoke_refresh_token(session: Session, raw_token: str) -> bool:
    token_hash = _token_hash(raw_token)
    record = session.exec(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    ).first()
    if record is None:
        return False
    if not record.revoked:
        record.revoked = True
        record.revoked_at = utcnow()
        session.add(record)
        session.commit()
    return True


def get_user_by_username(session: Session, username: str) -> Optional[User]:
    statement = select(User).where(User.username == username)
    return session.exec(statement).first()


def get_user_by_id(session: Session, user_id: int) -> Optional[User]:
    return session.get(User, user_id)


def authenticate_user(session: Session, username: str, password: str) -> Optional[User]:
    user = get_user_by_username(session, username)
    if not user or not user.is_active:
        logger.info("login failed: user=%s not found or inactive", username)
        return None
    if not verify_password(password, user.hashed_password):
        logger.info("login failed: user=%s bad password", username)
        return None
    logger.info("login success: user=%s", username)
    return user


def create_api_key(session: Session, user_id: int, name: str) -> tuple[ApiKey, str]:
    raw_key = f"psk_{secrets.token_urlsafe(32)}"
    record = ApiKey(
        user_id=user_id,
        name=name,
        key_hash=_token_hash(raw_key),
        prefix=raw_key[:12],
    )
    session.add(record)
    session.commit()
    session.refresh(record)
    return record, raw_key


def list_active_api_keys(session: Session, user_id: int) -> list[ApiKey]:
    return list(
        session.exec(
            select(ApiKey)
            .where(ApiKey.user_id == user_id)
            .where(ApiKey.revoked_at.is_(None))
            .order_by(ApiKey.created_at.desc())
        )
    )


def revoke_api_key(session: Session, user_id: int, key_id: int) -> bool:
    record = session.get(ApiKey, key_id)
    if record is None or record.user_id != user_id:
        return False
    if record.revoked_at is None:
        record.revoked_at = utcnow()
        session.add(record)
        session.commit()
    return True


def authenticate_api_key(
    session: Session, username: str, api_key: str
) -> Optional[User]:
    user = get_user_by_username(session, username)
    if not user or not user.is_active:
        logger.info("api key login failed: user=%s not found or inactive", username)
        return None
    record = session.exec(
        select(ApiKey)
        .where(ApiKey.user_id == user.id)
        .where(ApiKey.key_hash == _token_hash(api_key))
        .where(ApiKey.revoked_at.is_(None))
    ).first()
    if record is None:
        logger.info("api key login failed: user=%s bad key", username)
        return None
    record.last_used_at = utcnow()
    session.add(record)
    session.commit()
    logger.info("api key login success: user=%s", username)
    return user
