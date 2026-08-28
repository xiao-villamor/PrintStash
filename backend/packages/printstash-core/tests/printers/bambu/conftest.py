"""Transport doubles shared by the Bambu client's test modules.

Bambu is the only provider PrintStash talks to over two protocols at once —
MQTT for status and control, implicit-TLS FTPS for bytes — and neither can be
stood up in-process. Everything here therefore stands in for a socket, and each
double is written to enforce the *wire order* the real device requires rather
than merely to return a value: Bambu rejects a publish before the subscription
lands, and its FTPS server rejects a `STOR` before `PROT P`. A double that
accepted calls in any order would let a real ordering regression through.

`ScriptedMqttClient` is the interesting one. The client's own MQTT session runs
paho's network loop on another thread and hands results back through
`loop.call_soon_threadsafe`, so a double that only recorded calls could not
exercise the callbacks at all. This one drives them: `connect()` fires
`on_connect` with a configurable reason code, and `loop_start()` delivers each
queued message through `on_message`. That is enough to test the subscription
lifecycle — refusal, timeout, malformed payload, clean shutdown — without a
broker.

The credentials are the documented Bambu LAN-mode constants (`bblp` and the
printer's access code); `test-code` and `TEST-SERIAL` are obviously-fake stand-ins.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from printstash_core.printers.bambu import BambuClient
from printstash_core.printers.models import BambuConfig

HOST = "192.0.2.10"
SERIAL = "TEST-SERIAL"
ACCESS_CODE = "test-code"
REPORT_TOPIC = f"device/{SERIAL}/report"
REQUEST_TOPIC = f"device/{SERIAL}/request"


def make_client(**kwargs: Any) -> BambuClient:
    """A client for the documentation printer, with every seam injectable."""

    return BambuClient(BambuConfig(HOST, SERIAL, ACCESS_CODE), **kwargs)


class FakeMqttClient:
    """Records only the TLS and credential setup `_mqtt_client` performs."""

    def __init__(self) -> None:
        self.credentials: tuple[str, str] | None = None
        self.context: Any = None
        self.insecure: bool | None = None

    def username_pw_set(self, username: str, password: str) -> None:
        self.credentials = (username, password)

    def tls_set_context(self, context: Any) -> None:
        self.context = context

    def tls_insecure_set(self, insecure: bool) -> None:
        self.insecure = insecure


class ScriptedMqttClient(FakeMqttClient):
    """An MQTT client that drives the callbacks paho would drive.

    `connect()` fires `on_connect` with `reason_code`, and `loop_start()`
    delivers `messages` through `on_message`. A message may be raw `bytes` (to
    exercise the malformed-payload path) or an object that is JSON-encoded.
    Set `deliver=False` for the cases where the printer never answers, and
    `fire_connect=False` for the case where it never even acknowledges the
    connection.
    """

    def __init__(
        self,
        *,
        reason_code: object = 0,
        messages: list[object] | None = None,
        deliver: bool = True,
        publish_rc: int = 0,
        peer_common_name: str | None = None,
        fire_connect: bool = True,
    ) -> None:
        super().__init__()
        self.reason_code = reason_code
        self.fire_connect = fire_connect
        self.messages = list(messages or ())
        self.deliver = deliver
        self.publish_rc = publish_rc
        self.peer_common_name = peer_common_name
        self.calls: list[tuple[Any, ...]] = []
        self.on_connect: Any = None
        self.on_message: Any = None

    # `_validate_mqtt_peer` skips the identity check entirely when the client
    # exposes no socket, so this attribute only appears when a test asks for a
    # peer certificate.
    def __getattr__(self, name: str) -> Any:
        if name == "socket" and self.peer_common_name is not None:
            return self._socket
        raise AttributeError(name)

    def _socket(self) -> Any:
        common_name = self.peer_common_name

        class PeerSocket:
            def getpeercert(self) -> dict[str, object]:
                return {"subject": ((("commonName", common_name),),)}

        return PeerSocket()

    def connect(self, host: str, port: int, *, keepalive: int) -> None:
        self.calls.append(("connect", host, port, keepalive))
        if self.fire_connect:
            self.on_connect(self, None, None, self.reason_code)

    def subscribe(self, topic: str, *, qos: int) -> None:
        self.calls.append(("subscribe", topic, qos))

    def publish(self, topic: str, payload: str, *, qos: int, retain: bool) -> Any:
        self.calls.append(("publish", topic, json.loads(payload), qos, retain))

        class PublishInfo:
            rc = self.publish_rc

        return PublishInfo()

    def loop_start(self) -> None:
        self.calls.append(("loop_start",))
        if not self.deliver:
            return
        for message in self.messages:
            self._deliver(message)

    def _deliver(self, message: object) -> None:
        raw = message if isinstance(message, bytes) else json.dumps(message).encode()

        class Message:
            payload = raw

        self.on_message(self, None, Message())

    def disconnect(self) -> None:
        self.calls.append(("disconnect",))

    def loop_stop(self) -> None:
        self.calls.append(("loop_stop",))


class FakeFtpsClient:
    """An FTPS server that records the exact command sequence it received."""

    def __init__(
        self, *, remote_size: int | None = None, download: bytes = b""
    ) -> None:
        self.remote_size = remote_size
        self.download = download
        self.calls: list[tuple[Any, ...]] = []
        self.uploaded = b""

    def connect(self, host: str, port: int) -> None:
        self.calls.append(("connect", host, port))

    def login(self, username: str, password: str) -> None:
        self.calls.append(("login", username, password))

    def prot_p(self) -> None:
        self.calls.append(("prot_p",))

    def storbinary(self, command: str, source: Any) -> None:
        self.calls.append(("storbinary", command))
        self.uploaded = source.read()

    def size(self, remote_name: str) -> int | None:
        self.calls.append(("size", remote_name))
        if self.remote_size is not None:
            return self.remote_size
        return len(self.uploaded or self.download)

    def rename(self, source: str, destination: str) -> None:
        self.calls.append(("rename", source, destination))

    def retrbinary(self, command: str, callback: Any) -> None:
        self.calls.append(("retrbinary", command))
        callback(self.download)

    def quit(self) -> None:
        self.calls.append(("quit",))

    def close(self) -> None:
        self.calls.append(("close",))


class FailingFtpsClient(FakeFtpsClient):
    """Fails its first `connect`, then behaves — the retry-once shape."""

    def __init__(self, failure: BaseException | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.failure = failure

    def connect(self, host: str, port: int) -> None:
        super().connect(host, port)
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure


def connect_attempts(client: FakeFtpsClient) -> int:
    return len([call for call in client.calls if call[0] == "connect"])


@pytest.fixture
def source_file(tmp_path: Any) -> Any:
    """A small, valid local G-code file to transfer."""

    path = tmp_path / "cube.gcode"
    path.write_bytes(b"G28\n")
    return path
