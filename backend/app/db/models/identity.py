"""Actors, credentials, resource permissions, browser pairing and share grants."""

from datetime import datetime
from typing import ClassVar, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, column_property
from sqlmodel import Field, select

from app.core.time import utcnow
from app.db.encrypted import EncryptedText

from .base import SQLModel
from .library import LibraryRevision
from .types import CaptureProvider, CollectionRole, PrinterRole


class CollectionPermission(SQLModel, table=True):
    __tablename__ = "collection_permissions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "collection_id",
            name="uq_collection_permissions_user_collection",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    collection_id: int = Field(foreign_key="collections.id", index=True)
    role: CollectionRole = Field(index=True)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PrinterPermission(SQLModel, table=True):
    __tablename__ = "printer_permissions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "printer_id",
            name="uq_printer_permissions_user_printer",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    printer_id: int = Field(foreign_key="printers.id", index=True)
    role: PrinterRole = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class User(SQLModel, table=True):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("oidc_issuer", "oidc_subject", name="uq_users_oidc_identity"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(max_length=128, unique=True, index=True)
    email: Optional[str] = Field(default=None, max_length=255)
    hashed_password: str = Field(max_length=255)
    is_superuser: bool = Field(default=False)
    is_active: bool = Field(default=True)
    # Incrementing this value invalidates every access token issued for user.
    # Persisted in DB so logout survives API restarts and multiple processes.
    auth_version: int = Field(default=0)
    # External identity is additive: local users keep these null and local login
    # remains available even when OIDC is configured.
    oidc_issuer: Optional[str] = Field(default=None, max_length=512, index=True)
    oidc_subject: Optional[str] = Field(default=None, max_length=255, index=True)
    oidc_managed: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default="0", index=True),
    )

    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_tokens"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(max_length=64, unique=True, index=True)
    expires_at: datetime = Field(index=True)
    revoked: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    revoked_at: Optional[datetime] = None


class ApiKey(SQLModel, table=True):
    __tablename__ = "api_keys"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    name: str = Field(default="Programmatic access", max_length=128)
    key_hash: str = Field(max_length=64, unique=True, index=True)
    prefix: str = Field(max_length=16, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = Field(default=None, index=True)


class ProviderConnection(SQLModel, table=True):
    """One encrypted external-provider credential set owned by a user."""

    __tablename__ = "provider_connections"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider", name="uq_provider_connection_user_provider"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    provider: CaptureProvider = Field(
        sa_column=Column(String(32), nullable=False, index=True)
    )
    access_token: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )
    refresh_token: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )
    credential_secret: Optional[str] = Field(
        default=None, sa_column=Column(EncryptedText(), nullable=True)
    )
    token_expires_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ProviderOAuthState(SQLModel, table=True):
    __tablename__ = "provider_oauth_states"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    provider: CaptureProvider = Field(sa_column=Column(String(32), nullable=False))
    state_hash: str = Field(max_length=64, unique=True, index=True)
    redirect_uri: str = Field(max_length=2048)
    expires_at: datetime = Field(index=True)
    used_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)


class BrowserPairingCode(SQLModel, table=True):
    __tablename__ = "browser_pairing_codes"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    code_hash: str = Field(max_length=64, unique=True, index=True)
    expires_at: datetime = Field(index=True)
    used_at: Optional[datetime] = Field(default=None, index=True)
    attempts: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow)


class BrowserDevice(SQLModel, table=True):
    __tablename__ = "browser_devices"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_browser_device_user_name"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    edit_version: int = Field(
        default=1, sa_column=Column(BigInteger, nullable=False, server_default="1")
    )
    database_epoch: ClassVar[Mapped[str]]
    user_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    name: str = Field(max_length=128)
    credential_hash: str = Field(max_length=64, unique=True, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: Optional[datetime] = Field(default=None)
    revoked_at: Optional[datetime] = Field(default=None, index=True)


BrowserDevice.database_epoch = column_property(
    select(LibraryRevision.epoch).where(LibraryRevision.id == 1).scalar_subquery()
)


class ShareLink(SQLModel, table=True):
    """A public, expiring, read-only capability to view a single Model.

    Possession of the (unguessable) token grants access to exactly one model —
    never the rest of the vault, never any mutation. Only the SHA-256 of the
    token is stored; the raw token is shown once at creation.
    """

    __tablename__ = "share_links"

    id: Optional[int] = Field(default=None, primary_key=True)
    model_id: int = Field(foreign_key="models.id", index=True)
    token_hash: str = Field(max_length=64, unique=True, index=True)

    expires_at: datetime = Field(index=True)
    revoked_at: Optional[datetime] = Field(default=None, index=True)
    # When false, the public viewer can render the model but not download the
    # original source files (a tessellated mesh is still served for viewing).
    allow_download: bool = Field(default=False)
    selected_file_ids_json: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    access_count: int = Field(default=0)

    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
