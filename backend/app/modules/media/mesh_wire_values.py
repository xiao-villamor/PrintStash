"""Bounded worker codecs share tagged JSON values without importing supervision.

Bytes and tuples retain their existing wire shape; decoded objects are data,
never executable code. Frame owners enforce their aggregate byte limits.
"""

from __future__ import annotations

import base64
import math
from typing import Any


def pack_value(value: Any) -> Any:
    """JSON-safe form of a fingerprint value that keeps bytes and tuples distinct.

    Fingerprints carry raw descriptor blobs and tuples, which plain JSON would
    drop or flatten. Only these types are allowed: the reply comes from a process
    that has just parsed a hostile file, so the parent decodes data, never code
    (no pickle), and refuses anything it does not recognise.
    """
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("nonfinite descriptor value")
        return value
    if isinstance(value, bytes):
        return {"$bytes": base64.b64encode(value).decode("ascii")}
    if isinstance(value, tuple):
        return {"$tuple": [pack_value(item) for item in value]}
    if isinstance(value, list):
        return [pack_value(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) or key.startswith("$") for key in value):
            raise TypeError("unencodable mapping key")
        return {key: pack_value(item) for key, item in value.items()}
    raise TypeError(f"unencodable {type(value).__name__}")


def unpack_value(value: Any) -> Any:
    if isinstance(value, list):
        return [unpack_value(item) for item in value]
    if isinstance(value, dict):
        if len(value) == 1 and "$bytes" in value:
            if type(value["$bytes"]) is not str:
                raise ValueError("invalid bytes tag payload")
            return base64.b64decode(value["$bytes"], validate=True)
        if len(value) == 1 and "$tuple" in value:
            if type(value["$tuple"]) is not list:
                raise ValueError("invalid tuple tag payload")
            return tuple(unpack_value(item) for item in value["$tuple"])
        if any(not isinstance(key, str) or key.startswith("$") for key in value):
            raise ValueError("unknown tag")
        return {key: unpack_value(item) for key, item in value.items()}
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("invalid descriptor value")
