"""Notification channels and their recorded delivery outcomes."""

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column,
    Text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlmodel import Field

from app.core.time import utcnow
from app.db.encrypted import EncryptedText

from .base import SQLModel
from .types import NotificationDeliveryStatus, NotificationEventType, NotificationTarget


class NotificationChannel(SQLModel, table=True):
    """A configured outbound notification target (superuser-managed).

    Secret-bearing fields live in ``config_json`` (e.g. Telegram bot token,
    webhook URLs); the API masks them on read, mirroring the S3/MakerWorld
    secret handling. ``events_json`` and ``printer_ids_json`` hold the
    per-event and per-printer subscription filters as JSON arrays of strings;
    a null/empty ``printer_ids_json`` means "all printers".
    """

    __tablename__ = "notification_channels"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128)
    target: NotificationTarget = Field(index=True)
    enabled: bool = Field(default=True, index=True)

    # Target-specific connection config (url, bot_token, chat_id, topic, ...).
    config_json: str = Field(
        default="{}", sa_column=Column(EncryptedText(), nullable=False)
    )
    # JSON array of NotificationEventType values this channel subscribes to.
    events_json: str = Field(default="[]", sa_column=Column(Text))
    # JSON array of printer ids; null = all printers.
    printer_ids_json: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )

    # Visible "last notification" status surfaced in the UI.
    last_status: Optional[str] = Field(default=None, max_length=16)
    last_error: Optional[str] = Field(default=None, max_length=1024)
    last_delivered_at: Optional[datetime] = Field(default=None)

    # Circuit breaker: consecutive permanently-failed deliveries. Reset to 0 on
    # any success; once it crosses the threshold the channel is auto-disabled so
    # a dead endpoint stops generating failures for every event.
    consecutive_failures: int = Field(default=0)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class NotificationDelivery(SQLModel, table=True):
    """Outbox / work-queue row: one per (event x matching channel).

    Rows are inserted in the *same transaction* as the print-job/printer-status
    change that produced the event (transactional outbox), so events are never
    lost and — because emission is edge-triggered — never duplicated. A
    background dispatcher polls ``status in (pending)`` with
    ``next_retry_at <= now`` and delivers them with exponential backoff.

    ``context_json`` snapshots the rendered event context at enqueue time so a
    later channel-config edit can't retroactively change a queued payload.
    """

    __tablename__ = "notification_deliveries"

    id: Optional[int] = Field(default=None, primary_key=True)
    channel_id: int = Field(foreign_key="notification_channels.id", index=True)
    event_type: NotificationEventType = Field(
        sa_column=Column(
            SAEnum(NotificationEventType, native_enum=False, length=32),
            nullable=False,
            index=True,
        )
    )
    printer_id: Optional[int] = Field(
        default=None, foreign_key="printers.id", index=True
    )
    print_job_id: Optional[int] = Field(
        default=None, foreign_key="print_jobs.id", index=True
    )

    context_json: str = Field(default="{}", sa_column=Column(Text))

    status: NotificationDeliveryStatus = Field(
        default=NotificationDeliveryStatus.PENDING, index=True
    )
    attempts: int = Field(default=0)
    last_error: Optional[str] = Field(default=None, max_length=1024)
    next_retry_at: datetime = Field(default_factory=utcnow, index=True)
    delivered_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)
