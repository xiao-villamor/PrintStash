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
