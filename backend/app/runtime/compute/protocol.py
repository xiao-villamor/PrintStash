"""Bounded JSON socket protocol; never deserialize Python objects."""

import json
import math
import select
import socket
import struct
import time
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, TypeAdapter

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


class BinaryRenderRequest(WorkRequest):
    operation: Literal["render_binary"] = "render_binary"
    geometry_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    geometry_bytes: int = Field(gt=0, le=48 * 1024**2)
    header: str = Field(min_length=1, max_length=4096)
    recipe: str = Field(min_length=1, max_length=256)
    units: int = Field(ge=1)
    # Only the broker can attach validated arrays, never the wire decoder.
    _decoded: tuple | None = PrivateAttr(default=None)


Request = Annotated[
    StatusRequest | InferenceRequest | RenderRequest | BinaryRenderRequest,
    Field(discriminator="operation"),
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


def send_admitted(
    connection: socket.socket,
    payload: bytes,
    deadline: float,
    *,
    checkpoint=lambda: None,
) -> None:
    """Reserve broker staging before uploading a large request body.

    Capacity refusal is a normal CPU-routing outcome, not a broken GPU owner.
    Short writes retain cancellation/deadline checks under socket backpressure.
    """
    if len(payload) > MAX_FRAME:
        raise ComputeUnavailable(Reason.INVALID_INPUT)
    connection.sendall(struct.pack("!I", len(payload)))
    acknowledgement = json.loads(receive(connection, deadline, checkpoint=checkpoint))
    if "error" in acknowledgement:
        raise ComputeUnavailable(Reason(acknowledgement["error"]))
    if acknowledgement != {"ready": True}:
        raise ComputeUnavailable(Reason.INVALID_INPUT)
    send_body(connection, payload, deadline, checkpoint=checkpoint)


def send_body(
    connection, payload: bytes, deadline: float, *, checkpoint=lambda: None
) -> None:
    remaining = memoryview(payload)
    while remaining:
        checkpoint()
        if time.monotonic() >= deadline:
            raise ComputeUnavailable(Reason.DEADLINE)
        try:
            written = connection.send(remaining[:65536])
        except socket.timeout:
            continue
        if written == 0:
            raise ComputeUnavailable(Reason.CANCELLED)
        remaining = remaining[written:]


def encode(value) -> bytes:
    return json.dumps(value, allow_nan=False, separators=(",", ":")).encode()


def valid_deadline(deadline: float, now: float) -> bool:
    return math.isfinite(deadline) and now < deadline <= now + 300


def peer_disconnected(connection: socket.socket) -> bool:
    """Never let timeout-mode recv delay a completed ticket."""
    if not select.select([connection], [], [], 0)[0]:
        return False
    try:
        return connection.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT) == b""
    except BlockingIOError, socket.timeout:
        return False
