from __future__ import annotations

import logging
import re
import sys

from app.core.config import settings

_CONFIGURED = False
_QUERY_URL = re.compile(r"((?:https?://|/)[^\s\"'<>?]+)\?[^\s\"'<>]*")


class SensitiveQueryFilter(logging.Filter):
    """Scrub URL queries before any handler formats message or SDK traceback."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _QUERY_URL.sub(r"\1?[redacted]", str(record.msg))

        def scrub(value):
            return (
                _QUERY_URL.sub(r"\1?[redacted]", value)
                if isinstance(value, str)
                else value
            )

        if isinstance(record.args, tuple):
            record.args = tuple(scrub(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: scrub(value) for key, value in record.args.items()}
        if record.exc_info:
            record.exc_text = _QUERY_URL.sub(
                r"\1?[redacted]", logging.Formatter().formatException(record.exc_info)
            )
        return True


def _configure_root() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(SensitiveQueryFilter())
    for name in ("uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).addFilter(SensitiveQueryFilter())
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    _configure_root()
    return logging.getLogger(name)
