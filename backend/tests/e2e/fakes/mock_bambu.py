"""Protocol fakes for Bambu LAN MQTT and implicit FTPS transports.

MQTT fake implements authentication, TLS configuration, subscription, command
acknowledgements, and pushall status reports. FTPS fake records login and private
data-channel setup. No fake REST status API: Bambu LAN status arrives on MQTT's
``device/{serial}/report`` topic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable, Optional

from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode

from .print_sim import PrintSim

# -- Bambu print.gcode_state <-> PrintSim.state, mirrors
# BambuLanProvider._STATE_TO_MOONRAKER inverted. --------------------------
_SIM_TO_BAMBU_STATE = {
    "standby": "IDLE",
    "printing": "RUNNING",
    "paused": "PAUSE",
    "complete": "FINISH",
    "cancelled": "FINISH",  # Bambu has no distinct cancelled gcode_state.
    "error": "FAILED",
}


def _status_report(sim: PrintSim) -> dict[str, Any]:
    sim.progress()
    return {
        "print": {
            "gcode_state": _SIM_TO_BAMBU_STATE.get(sim.state, "IDLE"),
            "mc_percent": round(sim.progress() * 100),
            "subtask_name": sim.filename or None,
            "print_error": sim.message or "",
        }
    }


# -- Fake MQTT client (paho v2 surface: username_pw_set/tls_set/connect/
# loop_start/publish/loop_stop/disconnect) ---------------------------------


class _FakePublishInfo:
    def __init__(self, published: bool) -> None:
        self._published = published
        self.rc = 0

    def wait_for_publish(self, timeout: float | None = None) -> None:
        return None

    def is_published(self) -> bool:
        return self._published


class FakeMqttClient:
    """Paho-shaped client that returns Bambu report-topic messages."""

    def __init__(
        self,
        sim: PrintSim,
        *,
        expected_access_code: Optional[str] = None,
        reject_commands: bool = False,
    ) -> None:
        self.sim = sim
        self.expected_access_code = expected_access_code
        self.reject_commands = reject_commands
        self.published: list[dict[str, Any]] = []
        self.username: Optional[str] = None
        self.password: Optional[str] = None
        self.tls_configured = False
        self.subscriptions: list[tuple[str, int]] = []
        self.on_connect = None
        self.on_message = None

    def username_pw_set(self, username: str, password: Optional[str] = None) -> None:
        self.username = username
        self.password = password

    def tls_set_context(self, _context: Any) -> None:
        self.tls_configured = True

    def tls_insecure_set(self, _value: bool) -> None:
        return None

    def connect(self, host: str, port: int = 8883, keepalive: int = 30) -> None:
        reason_code = ReasonCode(PacketTypes.CONNACK, identifier=0)
        if (
            self.expected_access_code is not None
            and self.password != self.expected_access_code
        ):
            reason_code = ReasonCode(PacketTypes.CONNACK, identifier=135)
        if self.on_connect is not None:
            self.on_connect(self, None, {}, reason_code, None)

    def subscribe(self, topic: str, qos: int = 0) -> tuple[int, int]:
        self.subscriptions.append((topic, qos))
        return 0, len(self.subscriptions)

    def loop_start(self) -> None:
        return None

    def loop_stop(self) -> None:
        return None

    def disconnect(self) -> None:
        return None

    def publish(
        self, topic: str, payload: str, qos: int = 1, retain: bool = False
    ) -> _FakePublishInfo:
        body = json.loads(payload)
        self.published.append(body)
        report: dict[str, Any]
        if "pushing" in body:
            report = _status_report(self.sim)
        else:
            request = body.get("print", {})
            command = request.get("command")
            result = "failed" if self.reject_commands else "success"
            if not self.reject_commands:
                if command == "gcode_file":
                    param = request["param"]
                    self.sim.start(param.rsplit("/", 1)[-1])
                elif command == "pause":
                    self.sim.pause()
                elif command == "resume":
                    self.sim.resume()
                elif command == "stop":
                    self.sim.cancel()
            report = {
                "print": {
                    "command": command,
                    "sequence_id": request.get("sequence_id"),
                    "result": result,
                    "reason": "simulated rejection" if self.reject_commands else "",
                }
            }
        if self.on_message is not None:
            message = SimpleNamespace(
                topic=topic.replace("/request", "/report"),
                payload=json.dumps(report).encode(),
            )
            self.on_message(self, None, message)
        return _FakePublishInfo(published=True)


def make_mqtt_factory(
    sim: PrintSim,
    *,
    expected_access_code: Optional[str] = None,
    reject_commands: bool = False,
) -> tuple[Callable[[], FakeMqttClient], list[FakeMqttClient]]:
    """Raw-client factory injected below provider credential/TLS setup."""
    built: list[FakeMqttClient] = []

    def factory() -> FakeMqttClient:
        client = FakeMqttClient(
            sim,
            expected_access_code=expected_access_code,
            reject_commands=reject_commands,
        )
        built.append(client)
        return client

    return factory, built


# -- Fake implicit FTPS client (ftplib.FTP_TLS surface used by
# _upload_via_ftps: connect/login/prot_p/storbinary/size/rename/quit/close) -


@dataclass
class FakeFtpTls:
    files: dict[str, bytes] = field(default_factory=dict)
    expected_access_code: Optional[str] = None
    connected_host: Optional[str] = None
    username: Optional[str] = None
    private_data: bool = False

    def connect(self, host: str, port: int = 990) -> None:
        self.connected_host = host

    def login(self, user: str, passwd: str = "") -> None:
        self.username = user
        if (
            self.expected_access_code is not None
            and passwd != self.expected_access_code
        ):
            raise PermissionError("530 Login incorrect")

    def prot_p(self) -> None:
        self.private_data = True

    def storbinary(self, cmd: str, source) -> None:  # noqa: ANN001 - matches ftplib signature
        _, _, path = cmd.partition(" ")
        self.files[path] = source.read()

    def size(self, path: str) -> Optional[int]:
        data = self.files.get(path)
        return len(data) if data is not None else None

    def rename(self, from_path: str, to_path: str) -> None:
        self.files[to_path] = self.files.pop(from_path)

    def quit(self) -> None:
        return None

    def close(self) -> None:
        return None
