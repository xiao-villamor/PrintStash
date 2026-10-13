"""Durable capacity claims and bounded storage evidence."""

from datetime import datetime
from typing import ClassVar

from sqlalchemy import BigInteger, CheckConstraint, Column, Text
from sqlmodel import Field

from app.core.time import utcnow

from .base import SQLModel


class CapacityLock(SQLModel, table=True):
    # Machine bookkeeping, taken and released around every publication: user actions on work
    # are recorded explicitly, and a row-level trail of every heartbeat only
    # adds a second write to each of these transactions.
    __audit_exclude__: ClassVar[bool] = True

    __tablename__ = "capacity_locks"
    id: int = Field(default=1, primary_key=True)
    revision: int = Field(default=0)


class CapacityReservation(SQLModel, table=True):
    __audit_exclude__: ClassVar[bool] = True

    __tablename__ = "capacity_reservations"
    operation_id: str = Field(primary_key=True, max_length=200)
    resources_json: str = Field(sa_column=Column(Text, nullable=False))
    owner_identity_json: str = Field(
        default="{}", sa_column=Column(Text, nullable=False, server_default="{}")
    )
    expires_at: datetime = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow)


class CapacityAdmissionEvent(SQLModel, table=True):
    """Privacy-safe recent admission outcome; operation identifiers are omitted."""

    __tablename__ = "capacity_admission_events"
    id: int | None = Field(default=None, primary_key=True)
    occurred_at: datetime = Field(default_factory=utcnow, index=True)
    operation_kind: str = Field(max_length=64, index=True)
    decision: str = Field(max_length=16, index=True)
    reason: str = Field(max_length=64)
    required_bytes: int = Field(sa_column=Column(BigInteger, nullable=False))
    available_bytes: int | None = Field(
        default=None, sa_column=Column(BigInteger, nullable=True)
    )
    reserved_bytes: int = Field(sa_column=Column(BigInteger, nullable=False))
    headroom_bytes: int = Field(sa_column=Column(BigInteger, nullable=False))


class StorageInventorySample(SQLModel, table=True):
    __tablename__ = "storage_inventory_samples"
    __table_args__ = (
        CheckConstraint("owned_bytes >= 0", name="nonnegative_owned_bytes"),
    )
    id: int | None = Field(default=None, primary_key=True)
    sampled_at: datetime = Field(default_factory=utcnow, index=True)
    target_ref: str = Field(max_length=128, index=True)
    owned_bytes: int = Field(sa_column=Column(BigInteger, nullable=False))
    evidence_json: str = Field(sa_column=Column(Text, nullable=False))
