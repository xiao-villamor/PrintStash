from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session as SASession

from app.db.models import AuditLog

_actor_id_ctx: ContextVar[int | None] = ContextVar("audit_actor_id", default=None)
_ip_ctx: ContextVar[str | None] = ContextVar("audit_ip", default=None)
_installed = False


def set_audit_context(*, actor_id: int | None, ip: str | None) -> None:
    _actor_id_ctx.set(actor_id)
    _ip_ctx.set(ip)


def clear_audit_context() -> None:
    _actor_id_ctx.set(None)
    _ip_ctx.set(None)


def current_audit_context() -> tuple[int | None, str | None]:
    return _actor_id_ctx.get(), _ip_ctx.get()


_UNSET = object()

# Column names that must never appear in an audit diff, regardless of which
# table they belong to — printer/API credentials, password/token hashes, and
# opaque config blobs that embed secrets (e.g. NotificationChannel.config_json
# holds a Telegram bot token; see that model's docstring).
_REDACTED_FIELDS = {
    "api_key",
    "bambu_access_code",
    "prusalink_password",
    "prusalink_api_key",
    "elegoo_centauri_access_code",
    "octoprint_api_key",
    "hashed_password",
    "key_hash",
    "token_hash",
    "jwt_secret",
    "spoolman_api_key",
    "makerworld_token",
    "s3_secret_key",
    "s3_access_key",
    "backup_s3_secret_key",
    "backup_s3_access_key",
    "config_json",
}
_REDACTED = "[redacted]"


def record(
    session: SASession,
    *,
    action: str,
    resource_type: str,
    resource_id: int | None = None,
    diff: dict[str, Any] | None = None,
    actor_id: Any = _UNSET,
    ip: Any = _UNSET,
) -> None:
    """Write one ``AuditLog`` row directly and commit it.

    For actions that don't go through the ORM ``after_flush`` hook — e.g.
    backup/restore, which mutate the filesystem and swap the database file
    rather than changing tracked rows. Defaults to the ambient actor/IP
    context set by the request middleware; pass ``actor_id``/``ip`` explicitly
    to override (e.g. after a restore, where the pre-restore actor id may not
    exist in the restored database).
    """
    resolved_actor = _actor_id_ctx.get() if actor_id is _UNSET else actor_id
    resolved_ip = _ip_ctx.get() if ip is _UNSET else ip
    session.add(
        AuditLog(
            actor_id=resolved_actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            diff_json=json.dumps(diff or {}, default=str),
            ip=resolved_ip,
        )
    )
    session.commit()


def _resource_id(obj: Any) -> int | None:
    val = getattr(obj, "id", None)
    return int(val) if isinstance(val, int) else None


def _diff_for_obj(obj: Any) -> dict[str, Any]:
    state = inspect(obj)
    out: dict[str, Any] = {}
    for attr in state.attrs:
        hist = attr.history
        if not hist.has_changes():
            continue
        if attr.key in _REDACTED_FIELDS:
            out[attr.key] = {"before": _REDACTED, "after": _REDACTED}
            continue
        out[attr.key] = {
            "before": hist.deleted[0] if hist.deleted else None,
            "after": hist.added[0] if hist.added else getattr(obj, attr.key, None),
        }
    return out


def install_audit_listeners() -> None:
    global _installed
    if _installed:
        return

    @event.listens_for(SASession, "after_flush")
    def _after_flush(session: SASession, _ctx) -> None:
        actor_id = _actor_id_ctx.get()
        ip = _ip_ctx.get()
        rows: list[AuditLog] = []
        for obj in session.new:
            if isinstance(obj, AuditLog) or not hasattr(obj, "__tablename__"):
                continue
            rows.append(
                AuditLog(
                    actor_id=actor_id,
                    action="create",
                    resource_type=obj.__tablename__,
                    resource_id=_resource_id(obj),
                    diff_json=json.dumps(_diff_for_obj(obj), default=str),
                    ip=ip,
                )
            )
        for obj in session.dirty:
            if isinstance(obj, AuditLog) or not hasattr(obj, "__tablename__"):
                continue
            diff = _diff_for_obj(obj)
            if not diff:
                continue
            action = "update"
            if "deleted_at" in diff and diff["deleted_at"].get("after") is not None:
                action = "soft_delete"
            elif "deleted_at" in diff and diff["deleted_at"].get("after") is None:
                action = "restore"
            rows.append(
                AuditLog(
                    actor_id=actor_id,
                    action=action,
                    resource_type=obj.__tablename__,
                    resource_id=_resource_id(obj),
                    diff_json=json.dumps(diff, default=str),
                    ip=ip,
                )
            )
        for obj in session.deleted:
            if isinstance(obj, AuditLog) or not hasattr(obj, "__tablename__"):
                continue
            rows.append(
                AuditLog(
                    actor_id=actor_id,
                    action="hard_delete",
                    resource_type=obj.__tablename__,
                    resource_id=_resource_id(obj),
                    diff_json="{}",
                    ip=ip,
                )
            )
        if rows:
            session.add_all(rows)

    _installed = True
