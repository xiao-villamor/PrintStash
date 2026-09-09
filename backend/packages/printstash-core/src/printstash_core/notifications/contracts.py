"""Framework-neutral notification contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NotificationEventType(str, Enum):
    """Lifecycle events that a notification channel can render."""

    PRINT_COMPLETED = "print_completed"
    PRINT_FAILED = "print_failed"
    PRINT_CANCELLED = "print_cancelled"
    PRINTER_OFFLINE = "printer_offline"
    STORAGE_REGRESSION = "storage_regression"
    STORAGE_RECOVERY = "storage_recovery"
    STORAGE_AUDIT_FAILED = "storage_audit_failed"
    STORAGE_AUDIT_CANCELLED = "storage_audit_cancelled"
    STORAGE_AUDIT_OVERDUE = "storage_audit_overdue"
    STORAGE_REPAIR_FAILED = "storage_repair_failed"
    VAULT_MIGRATION = "vault_migration"


class NotificationTarget(str, Enum):
    """Supported outbound notification targets."""

    WEBHOOK = "webhook"
    DISCORD = "discord"
    TELEGRAM = "telegram"
    NTFY = "ntfy"


class RenderError(ValueError):
    """A channel configuration cannot be rendered for its target."""


@dataclass
class OutboundRequest:
    """A target-agnostic description of an outbound HTTP call."""

    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    json: Any | None = None
    data: str | None = None
