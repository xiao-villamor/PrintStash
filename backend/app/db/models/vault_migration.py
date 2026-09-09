"""Durable migration plans, exact copy proofs and the active Vault epoch."""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, CheckConstraint, Column, Text, UniqueConstraint
from sqlmodel import Field

from app.core.time import utcnow
from app.db.encrypted import EncryptedText

from .base import SQLModel


class VaultGeneration(SQLModel, table=True):
    __tablename__ = "vault_generations"  # pyright: ignore[reportAssignmentType]
    id: int = Field(default=1, primary_key=True)
    epoch: str = Field(default="0", max_length=64)
    activation_run_id: str | None = Field(default=None, max_length=64)
    manifest_sha256: str | None = Field(default=None, max_length=64)
    first_write_at: datetime | None = None


class VaultMigrationRun(SQLModel, table=True):
    __tablename__ = "vault_migration_runs"  # pyright: ignore[reportAssignmentType]
    id: str = Field(
        default_factory=lambda: uuid4().hex, primary_key=True, max_length=64
    )
    actor_id: int | None = Field(default=None, foreign_key="users.id")
    state: str = Field(default="planned", max_length=32, index=True)
    source_epoch: str = Field(max_length=64)
    destination_epoch: str = Field(
        default_factory=lambda: uuid4().hex, max_length=64, unique=True
    )
    source_config: str = Field(sa_column=Column(EncryptedText(), nullable=False))
    destination_config: str = Field(sa_column=Column(EncryptedText(), nullable=False))
    source_digest: str = Field(max_length=64)
    plan_digest: str = Field(max_length=64)
    manifest_sha256: str | None = Field(default=None, max_length=64)
    backup_id: str = Field(max_length=200)
    backup_source_ref: str | None = Field(default=None, max_length=256)
    backup_summary_json: str = Field(
        default="{}", sa_column=Column(Text, nullable=False)
    )
    expires_at: datetime
    created_at: datetime = Field(default_factory=utcnow)
    activated_at: datetime | None = None
    cleanup_after: datetime | None = None
    error_code: str | None = Field(default=None, max_length=128)
    cleanup_findings: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    journal_nonce: str = Field(
        default_factory=lambda: uuid4().hex, max_length=64, unique=True
    )
    source_provider_ref: str = Field(default="", max_length=64)
    destination_provider_ref: str = Field(default="", max_length=64)
    source_summary_json: str = Field(
        default="{}", sa_column=Column(Text, nullable=False)
    )
    destination_summary_json: str = Field(
        default="{}", sa_column=Column(Text, nullable=False)
    )
    policy_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    phase_history_json: str = Field(
        default="[]", sa_column=Column(Text, nullable=False)
    )
    started_at: datetime | None = None
    last_activity_at: datetime = Field(default_factory=utcnow)
    retryable: bool = False
    cleanup_outcome: str | None = Field(default=None, max_length=32)
    pre_audit_id: int | None = Field(default=None, foreign_key="vault_audit_runs.id")
    post_audit_id: int | None = Field(default=None, foreign_key="vault_audit_runs.id")
    full_audit_id: int | None = Field(default=None, foreign_key="vault_audit_runs.id")
    notification_events: int = 0


class VaultMigrationObject(SQLModel, table=True):
    __tablename__ = "vault_migration_objects"  # pyright: ignore[reportAssignmentType]
    __table_args__ = (
        UniqueConstraint("run_id", "source_key_digest", name="uq_migration_source_key"),
        CheckConstraint("size_bytes >= 0", name="migration_size_nonnegative"),
    )
    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(
        foreign_key="vault_migration_runs.id", index=True, max_length=64
    )
    source_key_digest: str = Field(max_length=64)
    source_key: str = Field(sa_column=Column(Text, nullable=False))
    destination_key: str = Field(sa_column=Column(Text, nullable=False))
    resource_type: str = Field(max_length=64)
    resource_id: str = Field(max_length=128)
    size_bytes: int = Field(sa_column=Column(BigInteger, nullable=False))
    sha256: str = Field(max_length=64)
    source_receipt: str | None = Field(default=None, sa_column=Column(Text))
    destination_receipt: str | None = Field(default=None, sa_column=Column(Text))
    state: str = Field(default="pending", max_length=32, index=True)
    verified_at: datetime | None = None
    error_code: str | None = Field(default=None, max_length=128)
    retryable: bool = False
    baseline: bool = True
    in_final: bool = True
    attempts: int = 0
