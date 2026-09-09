"""External fsynced activation evidence, readable before database migrations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock

from app.core.config import settings

_lock = RLock()


def journal_path() -> Path:
    return Path(settings.staging_dir) / "vault-migration" / "activation.jsonl"


def append(event: dict[str, object]) -> None:
    with _lock:
        _append(event)


def _append(event: dict[str, object]) -> None:
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        offset = 0
        while offset < len(payload):
            offset += os.write(fd, payload[offset:])
        os.fsync(fd)
    finally:
        os.close(fd)
    parent = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def events() -> list[dict[str, object]]:
    try:
        lines = journal_path().read_text().splitlines()
    except FileNotFoundError:
        return []
    result = []
    for line in lines:
        value = json.loads(line)
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("phase"), str)
            or not isinstance(value.get("run_id"), str)
            or not isinstance(value.get("nonce"), str)
        ):
            raise ValueError("migration_journal_invalid")
        result.append(value)
    return result


def inspect_before_writes() -> bool:
    """Any incomplete/invalid intent requires recovery before new writes."""
    from app.runtime.maintenance import hold_restore_maintenance

    try:
        evidence = events()
        latest = {}
        for event in evidence:
            if event["phase"] != "first_write":
                latest[event["run_id"]] = event["phase"]
        unresolved = any(
            phase not in {"complete", "discarded", "aborted"}
            for phase in latest.values()
        )
    except (OSError, ValueError, UnicodeError):
        unresolved = True
    if unresolved:
        hold_restore_maintenance()
    return unresolved
