"""Bounded JSON socket protocol; never deserialize Python objects."""

import json
import math
import socket
import struct
import time
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .contracts import ComputeUnavailable, Reason

MAX_FRAME = 96 * 1024**2


class Priority(StrEnum):
    INTERACTIVE = "interactive"
    BACKGROUND = "background"


class StatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["status"] = "status"


class WorkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deadline: float = Field(gt=0, allow_inf_nan=False)
    priority: Priority
    request_id: str = Field(min_length=1, max_length=128)


class InferenceRequest(WorkRequest):
    operation: Literal["inference"] = "inference"
    directory: str = Field(min_length=1, max_length=4096)
    model_key: str = Field(min_length=1, max_length=256)
    threads: int = Field(ge=1, le=4)
    payload: str = Field(max_length=48 * 1024**2)


class RenderRequest(WorkRequest):
    operation: Literal["render"] = "render"
    payload: str = Field(max_length=90 * 1024**2)
    recipe: str = Field(min_length=1, max_length=256)
    units: int = Field(ge=1)


Request = Annotated[
    StatusRequest | InferenceRequest | RenderRequest, Field(discriminator="operation")
]
request_type = TypeAdapter(Request)


def _read(connection: socket.socket, length: int, deadline: float, checkpoint) -> bytes:
    result = bytearray()
    while len(result) < length:
        checkpoint()
        if time.monotonic() >= deadline:
            raise ComputeUnavailable(Reason.DEADLINE)
        try:
            chunk = connection.recv(min(length - len(result), 65536))
        except socket.timeout:
            continue
        if not chunk:
            raise ComputeUnavailable(Reason.CANCELLED)
        result.extend(chunk)
    return bytes(result)


def receive(
    connection: socket.socket,
    deadline: float,
    *,
    checkpoint=lambda: None,
    admit=lambda length: None,
) -> bytes:
    length = struct.unpack("!I", _read(connection, 4, deadline, checkpoint))[0]
    if length > MAX_FRAME:
        raise ComputeUnavailable(Reason.INVALID_INPUT)
    admit(length)
    return _read(connection, length, deadline, checkpoint)


def send(connection: socket.socket, payload: bytes) -> None:
    if len(payload) > MAX_FRAME:
        raise ComputeUnavailable(Reason.INVALID_INPUT)
    connection.sendall(struct.pack("!I", len(payload)) + payload)


def encode(value) -> bytes:
    return json.dumps(value, allow_nan=False, separators=(",", ":")).encode()


def valid_deadline(deadline: float, now: float) -> bool:
    return math.isfinite(deadline) and now < deadline <= now + 300
