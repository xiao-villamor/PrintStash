"""Provider abstraction for printer backends.

Adding a provider means writing one class: declare its ``provider`` enum member
and its ``capabilities``, point it at a client, implement ``build()``, and
decorate it with ``@register``. Everything else — the not-supported responses,
the error translation, the capability lookup, the credential error — comes from
``BaseProvider`` and the registry below.

The capability set is the single source of truth: a method whose capability a
provider does not declare raises ``operation_not_supported_for_provider``
without touching the network. Providers never hand-write those stubs.
"""

from __future__ import annotations

import asyncio
import json
import socket
import ssl
import threading
from dataclasses import dataclass
from enum import StrEnum
from ftplib import FTP_TLS  # nosec B402 - Bambu LAN's implicit-TLS FTPS, not plaintext
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Protocol, runtime_checkable
from uuid import uuid4

import paho.mqtt.client as mqtt

from app.core.logging import get_logger
from app.db.models import Printer, PrinterProvider
from app.services.elegoo_centauri import ElegooCentauriClient, ElegooCentauriError
from app.services.moonraker import MoonrakerClient, MoonrakerError
from app.services.octoprint import OctoPrintClient, OctoPrintError
from app.services.prusalink import PrusaLinkClient, PrusaLinkError

logger = get_logger(__name__)

# Bambu LAN printers use a private BBL certificate hierarchy. MQTT connects by
# IP address, while the leaf certificate's common name is the printer serial,
# so normal DNS hostname verification cannot be used. Keep chain validation on
# and validate that serial explicitly after TLS negotiation.
_BAMBU_CA_CERTIFICATES = """-----BEGIN CERTIFICATE-----
MIIFfzCCA2egAwIBAgIUXtzR6tRiL/RHBRXOoyFU0+XrliowDQYJKoZIhvcNAQEL
BQAwRjELMAkGA1UEBhMCQ04xITAfBgNVBAoMGEJCTCBUZWNobm9sb2dpZXMgQ28u
IEx0ZDEUMBIGA1UEAwwLQkJMIENBMiBSU0EwIBcNMjUwNjE3MDEzODA4WhgPMjA1
MDA2MTcwMTM4MDhaMEYxCzAJBgNVBAYTAkNOMSEwHwYDVQQKDBhCQkwgVGVjaG5v
bG9naWVzIENvLiBMdGQxFDASBgNVBAMMC0JCTCBDQTIgUlNBMIICIjANBgkqhkiG
9w0BAQEFAAOCAg8AMIICCgKCAgEAo4550G4c42gTKzQqixwKT089RizIdZpyOcGA
679rPaOdWsMqVwnYPP2FpMqXKkjFbedE+SpGloi2NKCuiPNVRbq9PHOOZwTs7YLo
bOwf53FJuO6vRFpzFfX1tlc9zlFqJvZnYO9NgHpMysidocWcgrDN/SIDywgPB5CV
bYg3Vvzua9fwZx9e5KT9xd5IpTqdTrWS47jQOVKLhdQCbJFIlMrblOwLBAx+fHok
wqh6tkI6Ktuyyjw8Dysebi1ndWjKtZ2mW47r8xZ/J+z3EZqcyJMY6MRtx/zb1jBF
uHtkjrb5Kv1DMzSKlkaNJIbvC+Mk+hI97W+SjLSRuIdC7+oJUzWaSzgu9cjXCVfm
q8t4IL/35hP69PK95LgLectIrP96CYAT/aVMG19FrFW0QWEyfT+kzG4jkumfPbHq
Y2nNkEN0+tjj3h4WdzrWgQEojK/lhfcRFVkts74+aZoMpQP+vmL17CKmSzXk5o/e
K21xgxJdzMbdztfTpibiXk0abfOpN+1VR+3NYa+bROAKNyGaReEGsyW2bjcjNx51
5Vqzj3SVxhMSp5vfF9E4A1jE99M/l9jQDM6RzkT0lMccGAd5tUSdNvDlrqtQaQiK
v/ZsXPgXLTWfOpvaLNEgwdMgZMuhjpkwvAZyoYfeF9kyydjDh7bvrX//cz/VopAU
lxUtQtMCAwEAAaNjMGEwHQYDVR0OBBYEFNVJgQad1sNTN0jxVkwbJ/XM1an1MB8G
A1UdIwQYMBaAFNVJgQad1sNTN0jxVkwbJ/XM1an1MA8GA1UdEwEB/wQFMAMBAf8w
DgYDVR0PAQH/BAQDAgEGMA0GCSqGSIb3DQEBCwUAA4ICAQBFZDKMJfp/N4gBeFHh
MiFehaUyMS6e9mzrTfMLJLJoj6Jopa9V9jIfcCEBGZuRThqFcATV+UdFHSINpUcH
upcCYnazTRC4dn1hnxnQ1ojQcHxdGp9xGw/YclAKD97d8bPShfBMT1to9zbMK7T5
L8zgqg01YIOKjQk0Hcd0+0iUr6m8zQ5P8Rl3QXqAyeWgqmYQrrjTWwPsgdfHNXKX
vDrx7/cqry5lKU802hUplKMBxelv4W8407Ytj1lfJOwvxqxxsFU5jSwcUG3zo2vk
QtjRs8m5BKup5K1OPYkkPu7Ld89X0XpU073/dNDG11uxb1eDKrtNP6vZuZjNE2Pq
8HCoI1EtP+ItyqtUMvHi6Z2zsmlA25broVioeUKxjlIecpQ9JR/FhDu9CWNF/nDW
LSORNaMMzgsMSzI+HCiUhqN+qMIvVP6rzGTJzwqz/lc5Lf+ZPCnGA9WJTT4uPIhf
ufbZmnUJ35WuWKHxovDsqBh88zQ9sZ+ei4Hi4vVzOhUgfG3aLoSQEYqRoqaboANh
wCwzyuW2Rv54u5QSBbd6Gx1OpvsWmLPWd2/iL2kISl5wfmLGVydvSJa+rbOfuAy7
ycVQacVDQCAnbhoVrQy7+454QsKSW3ZV6BcyRrorewCyCYgd7nyxflxHZTBEykXX
haGNe/KFNvJBMOIuIUzknRRmiQ==
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIB8zCCAZmgAwIBAgIUe61jGQ4RzIC8k+sNuqbI/CaNqPIwCgYIKoZIzj0EAwIw
RjELMAkGA1UEBhMCQ04xITAfBgNVBAoMGEJCTCBUZWNobm9sb2dpZXMgQ28uIEx0
ZDEUMBIGA1UEAwwLQkJMIENBMiBFQ0MwIBcNMjUwNjE3MDEzODM1WhgPMjA1MDA2
MTcwMTM4MzVaMEYxCzAJBgNVBAYTAkNOMSEwHwYDVQQKDBhCQkwgVGVjaG5vbG9n
aWVzIENvLiBMdGQxFDASBgNVBAMMC0JCTCBDQTIgRUNDMFkwEwYHKoZIzj0CAQYI
KoZIzj0DAQcDQgAEpKTF7wRSty4DXpGJzgCPwRh8ghLlxUC3qJbyEgLqTvJgbiwY
APPHK7kVbVmerkqhHOT4QeWRlTG3dOQGLA2VpaNjMGEwHQYDVR0OBBYEFKuRpsjY
REOyIKH7HwOE6jhGBd6NMB8GA1UdIwQYMBaAFKuRpsjYREOyIKH7HwOE6jhGBd6N
MA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0PAQH/BAQDAgEGMAoGCCqGSM49BAMCA0gA
MEUCIErBiUm3VdtP3rz4kb8aLpI5p+BzL7M9vElBGWWJxpHMAiEA3r5tJWVGwuxi
YCrB1c40KYFRFyahGrhOJZAj/YhRdnU=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIDZTCCAk2gAwIBAgIUV1FckwXElyek1onFnQ9kL7Bk4N8wDQYJKoZIhvcNAQEL
BQAwQjELMAkGA1UEBhMCQ04xIjAgBgNVBAoMGUJCTCBUZWNobm9sb2dpZXMgQ28u
LCBMdGQxDzANBgNVBAMMBkJCTCBDQTAeFw0yMjA0MDQwMzQyMTFaFw0zMjA0MDEw
MzQyMTFaMEIxCzAJBgNVBAYTAkNOMSIwIAYDVQQKDBlCQkwgVGVjaG5vbG9naWVz
IENvLiwgTHRkMQ8wDQYDVQQDDAZCQkwgQ0EwggEiMA0GCSqGSIb3DQEBAQUAA4IB
DwAwggEKAoIBAQDL3pnDdxGOk5Z6vugiT4dpM0ju+3Xatxz09UY7mbj4tkIdby4H
oeEdiYSZjc5LJngJuCHwtEbBJt1BriRdSVrF6M9D2UaBDyamEo0dxwSaVxZiDVWC
eeCPdELpFZdEhSNTaT4O7zgvcnFsfHMa/0vMAkvE7i0qp3mjEzYLfz60axcDoJLk
p7n6xKXI+cJbA4IlToFjpSldPmC+ynOo7YAOsXt7AYKY6Glz0BwUVzSJxU+/+VFy
/QrmYGNwlrQtdREHeRi0SNK32x1+bOndfJP0sojuIrDjKsdCLye5CSZIvqnbowwW
1jRwZgTBR29Zp2nzCoxJYcU9TSQp/4KZuWNVAgMBAAGjUzBRMB0GA1UdDgQWBBSP
NEJo3GdOj8QinsV8SeWr3US+HjAfBgNVHSMEGDAWgBSPNEJo3GdOj8QinsV8SeWr
3US+HjAPBgNVHRMBAf8EBTADAQH/MA0GCSqGSIb3DQEBCwUAA4IBAQABlBIT5ZeG
fgcK1LOh1CN9sTzxMCLbtTPFF1NGGA13mApu6j1h5YELbSKcUqfXzMnVeAb06Htu
3CoCoe+wj7LONTFO++vBm2/if6Jt/DUw1CAEcNyqeh6ES0NX8LJRVSe0qdTxPJuA
BdOoo96iX89rRPoxeed1cpq5hZwbeka3+CJGV76itWp35Up5rmmUqrlyQOr/Wax6
itosIzG0MfhgUzU51A2P/hSnD3NDMXv+wUY/AvqgIL7u7fbDKnku1GzEKIkfH8hm
Rs6d8SCU89xyrwzQ0PR853irHas3WrHVqab3P+qNwR0YirL0Qk7Xt/q3O1griNg2
Blbjg3obpHo9
-----END CERTIFICATE-----"""


class _ImplicitFTP_TLS(FTP_TLS):
    """``ftplib`` client variant for Bambu's implicit-TLS port 990."""

    def connect(self, host="", port=0, timeout=-999, source_address=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.source_address = source_address
        self.sock = socket.create_connection(
            (host, port), timeout, source_address=source_address
        )
        self.af = self.sock.family
        self.sock = self.context.wrap_socket(self.sock, server_hostname=host)
        self.file = self.sock.makefile("r", encoding=self.encoding)
        self.welcome = self.getresp()
        return self.welcome


class ProviderError(RuntimeError):
    """Common provider exception surface."""

    def __init__(self, detail: str, *, code: str = "provider_error"):
        super().__init__(detail)
        self.detail = detail
        self.code = code


class Capability(StrEnum):
    """Provider action vocabulary shared by API, UI, and future edge transport."""

    START = "start"
    PAUSE = "pause"
    RESUME = "resume"
    CANCEL = "cancel"
    LIVE_STATUS = "live_status"
    UPLOAD = "upload"
    LIST_FILES = "list_files"
    SEND_GCODE = "send_gcode"
    MEASURED_CONSUMPTION = "measured_consumption"
    # Not surfaced as UI action flags, but gated the same way.
    DELETE_FILE = "delete_file"
    EMERGENCY_STOP = "emergency_stop"
    SERVER_INFO = "server_info"
    SERVER_CONFIG = "server_config"
    PRINTER_CONFIG = "printer_config"


# Provider method -> capability that must be declared for it to run. Methods
# absent here (info, subscribe_status) are mandatory for every provider.
_METHOD_CAPABILITY: dict[str, Capability] = {
    "query_status": Capability.LIVE_STATUS,
    "list_files": Capability.LIST_FILES,
    "upload": Capability.UPLOAD,
    "delete_file": Capability.DELETE_FILE,
    "start": Capability.START,
    "pause": Capability.PAUSE,
    "resume": Capability.RESUME,
    "cancel": Capability.CANCEL,
    "run_gcode": Capability.SEND_GCODE,
    "emergency_stop": Capability.EMERGENCY_STOP,
    "server_info": Capability.SERVER_INFO,
    "server_config": Capability.SERVER_CONFIG,
    "printer_config": Capability.PRINTER_CONFIG,
}


@dataclass(frozen=True)
class ProviderCapabilities:
    supported: frozenset[Capability]
    support_level: str = "stable"
    support_notes: tuple[str, ...] = ()
    requires_ready_before_send: bool = False

    def supports(self, capability: Capability) -> bool:
        return capability in self.supported

    @property
    def can_start(self) -> bool:
        return self.supports(Capability.START)

    @property
    def can_pause(self) -> bool:
        return self.supports(Capability.PAUSE)

    @property
    def can_resume(self) -> bool:
        return self.supports(Capability.RESUME)

    @property
    def can_cancel(self) -> bool:
        return self.supports(Capability.CANCEL)

    @property
    def can_live_status(self) -> bool:
        return self.supports(Capability.LIVE_STATUS)

    @property
    def can_upload(self) -> bool:
        return self.supports(Capability.UPLOAD)

    @property
    def can_list_files(self) -> bool:
        return self.supports(Capability.LIST_FILES)

    @property
    def can_send_gcode(self) -> bool:
        return self.supports(Capability.SEND_GCODE)

    @property
    def can_measure_consumption(self) -> bool:
        return self.supports(Capability.MEASURED_CONSUMPTION)

    @property
    def unsupported_actions(self) -> tuple[str, ...]:
        """User-facing action names this provider cannot perform.

        Derived from ``supported`` rather than hand-listed, so a capability set
        and the "what's missing" copy can never drift apart.
        """
        return tuple(
            cap.value for cap in _UNSUPPORTED_ACTION_ORDER if not self.supports(cap)
        )

    def action_flags(self) -> dict[str, bool]:
        return {
            "can_start": self.can_start,
            "can_pause": self.can_pause,
            "can_resume": self.can_resume,
            "can_cancel": self.can_cancel,
            "can_live_status": self.can_live_status,
            "can_upload": self.can_upload,
            "can_list_files": self.can_list_files,
            "can_send_gcode": self.can_send_gcode,
            "can_measure_consumption": self.can_measure_consumption,
        }

    def as_api_dict(self) -> dict[str, object]:
        return {
            **self.action_flags(),
            "support_level": self.support_level,
            "support_notes": list(self.support_notes),
            "unsupported_actions": list(self.unsupported_actions),
        }


# Order the UI shows missing actions in; also fixes which capabilities are worth
# naming to a user (SERVER_CONFIG etc. are internal plumbing, not user actions).
_UNSUPPORTED_ACTION_ORDER: tuple[Capability, ...] = (
    Capability.UPLOAD,
    Capability.LIST_FILES,
    Capability.DELETE_FILE,
    Capability.SEND_GCODE,
    Capability.EMERGENCY_STOP,
    Capability.MEASURED_CONSUMPTION,
)


@runtime_checkable
class PrinterProviderClient(Protocol):
    capabilities: ProviderCapabilities

    async def info(self) -> dict[str, Any]: ...

    async def server_info(self) -> dict[str, Any]: ...

    async def server_config(self) -> dict[str, Any]: ...

    async def printer_config(self) -> dict[str, Any]: ...

    async def query_status(self) -> dict[str, Any]: ...

    async def list_files(self) -> list[dict[str, Any]]: ...

    async def upload(
        self, local_path: Path, remote_filename: str
    ) -> dict[str, Any]: ...

    async def delete_file(self, remote_filename: str) -> dict[str, Any]: ...

    async def start(self, remote_filename: str) -> dict[str, Any]: ...

    async def pause(self) -> dict[str, Any]: ...

    async def resume(self) -> dict[str, Any]: ...

    async def cancel(self) -> dict[str, Any]: ...

    async def run_gcode(self, script: str) -> dict[str, Any]: ...

    async def emergency_stop(self) -> dict[str, Any]: ...

    async def subscribe_status(
        self,
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None: ...


def _require(printer: Printer, *fields: str) -> None:
    """Reject a printer row missing any credential field this provider needs."""
    if any(not getattr(printer, field, None) for field in fields):
        raise ProviderError(
            "provider_credentials_missing", code="provider_credentials_missing"
        )


class BaseProvider:
    """Capability-gated default implementation of every provider method.

    Subclasses override only what they actually support; the rest raises
    ``operation_not_supported_for_provider`` before any I/O happens.
    """

    provider: PrinterProvider
    capabilities: ProviderCapabilities

    @classmethod
    def build(cls, printer: Printer) -> "BaseProvider":  # pragma: no cover - abstract
        raise NotImplementedError

    def _check(self, method: str) -> None:
        capability = _METHOD_CAPABILITY.get(method)
        if capability is not None and not self.capabilities.supports(capability):
            raise ProviderError(
                "operation_not_supported_for_provider",
                code="operation_not_supported_for_provider",
            )

    async def info(self) -> dict[str, Any]:
        raise NotImplementedError

    async def server_info(self) -> dict[str, Any]:
        self._check("server_info")
        return await self.info()

    async def server_config(self) -> dict[str, Any]:
        self._check("server_config")
        raise NotImplementedError

    async def printer_config(self) -> dict[str, Any]:
        self._check("printer_config")
        raise NotImplementedError

    async def query_status(self) -> dict[str, Any]:
        self._check("query_status")
        raise NotImplementedError

    async def list_files(self) -> list[dict[str, Any]]:
        self._check("list_files")
        raise NotImplementedError

    async def upload(self, local_path: Path, remote_filename: str) -> dict[str, Any]:
        self._check("upload")
        raise NotImplementedError

    async def delete_file(self, remote_filename: str) -> dict[str, Any]:
        self._check("delete_file")
        raise NotImplementedError

    async def start(self, remote_filename: str) -> dict[str, Any]:
        self._check("start")
        raise NotImplementedError

    async def pause(self) -> dict[str, Any]:
        self._check("pause")
        raise NotImplementedError

    async def resume(self) -> dict[str, Any]:
        self._check("resume")
        raise NotImplementedError

    async def cancel(self) -> dict[str, Any]:
        self._check("cancel")
        raise NotImplementedError

    async def run_gcode(self, script: str) -> dict[str, Any]:
        self._check("run_gcode")
        raise NotImplementedError

    async def emergency_stop(self) -> dict[str, Any]:
        self._check("emergency_stop")
        raise NotImplementedError

    async def subscribe_status(
        self,
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        raise NotImplementedError


class DelegatingProvider(BaseProvider):
    """Provider whose client already speaks the provider vocabulary.

    Every supported method forwards to the client method of the same name
    (unless ``method_map`` renames it) and translates ``client_error`` into
    ``ProviderError``. That is all PrusaLink/OctoPrint/Elegoo/Moonraker need.
    """

    client_error: type[Exception]
    method_map: dict[str, str] = {}

    def __init__(self, client: Any) -> None:
        self.client = client

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        self._check(method)
        try:
            target = getattr(self.client, self.method_map.get(method, method))
            return await target(*args, **kwargs)
        except self.client_error as exc:
            raise ProviderError(
                str(exc), code=getattr(exc, "code", "provider_transport_error")
            ) from exc

    async def info(self) -> dict[str, Any]:
        return await self._call("info")

    async def server_info(self) -> dict[str, Any]:
        self._check("server_info")
        return await self.info()

    async def server_config(self) -> dict[str, Any]:
        return await self._call("server_config")

    async def printer_config(self) -> dict[str, Any]:
        return await self._call("printer_config")

    async def query_status(self) -> dict[str, Any]:
        return await self._call("query_status")

    async def list_files(self) -> list[dict[str, Any]]:
        return await self._call("list_files")

    async def upload(self, local_path: Path, remote_filename: str) -> dict[str, Any]:
        return await self._call("upload", local_path, remote_filename)

    async def delete_file(self, remote_filename: str) -> dict[str, Any]:
        return await self._call("delete_file", remote_filename)

    async def start(self, remote_filename: str) -> dict[str, Any]:
        return await self._call("start", remote_filename)

    async def pause(self) -> dict[str, Any]:
        return await self._call("pause")

    async def resume(self) -> dict[str, Any]:
        return await self._call("resume")

    async def cancel(self) -> dict[str, Any]:
        return await self._call("cancel")

    async def run_gcode(self, script: str) -> dict[str, Any]:
        return await self._call("run_gcode", script)

    async def emergency_stop(self) -> dict[str, Any]:
        return await self._call("emergency_stop")

    async def subscribe_status(
        self,
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        await self._call("subscribe_status", on_status, stop_event=stop_event)


PROVIDERS: dict[PrinterProvider, type[BaseProvider]] = {}


def register(cls: type[BaseProvider]) -> type[BaseProvider]:
    PROVIDERS[cls.provider] = cls
    return cls


@register
class MoonrakerProvider(DelegatingProvider):
    provider = PrinterProvider.MOONRAKER
    capabilities = ProviderCapabilities(
        supported=frozenset(Capability),
        support_level="stable",
    )
    client_error = MoonrakerError
    method_map = {
        "printer_config": "query_configfile",
        "list_files": "list_gcode_files",
        "upload": "upload_gcode",
        "start": "start_print",
        "pause": "pause_print",
        "resume": "resume_print",
        "cancel": "cancel_print",
        "delete_file": "delete_gcode_file",
        "subscribe_status": "subscribe",
    }

    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        super().__init__(MoonrakerClient(base_url, api_key))

    @classmethod
    def build(cls, printer: Printer) -> "MoonrakerProvider":
        _require(printer, "moonraker_url")
        return cls(printer.moonraker_url, printer.api_key)

    async def list_files(self) -> list[dict[str, Any]]:
        body = await self._call("list_files")
        result = body.get("result", [])
        return result if isinstance(result, list) else []

    async def upload(self, local_path: Path, remote_filename: str) -> dict[str, Any]:
        return await self._call(
            "upload", local_path, remote_filename, start_print=False
        )


@register
class BambuLanProvider(BaseProvider):
    provider = PrinterProvider.BAMBU_LAN
    capabilities = ProviderCapabilities(
        supported=frozenset(
            {
                Capability.START,
                Capability.PAUSE,
                Capability.RESUME,
                Capability.CANCEL,
                Capability.LIVE_STATUS,
                Capability.UPLOAD,
            }
        ),
        support_level="beta",
        support_notes=(
            "Bambu LAN upload and explicit start are beta features.",
            "Printer file inventory, deletion, raw G-code controls, and measured filament consumption are unavailable.",
        ),
        requires_ready_before_send=True,
    )

    def __init__(
        self,
        host: str,
        serial: str,
        access_code: str,
        *,
        mqtt_client_factory: Callable[[], mqtt.Client] | None = None,
    ) -> None:
        self.host = host
        self.serial = serial
        self.access_code = access_code
        self._mqtt_client_factory = mqtt_client_factory
        self._request_topic = f"device/{serial}/request"
        self._report_topic = f"device/{serial}/report"

    @classmethod
    def build(cls, printer: Printer) -> "BambuLanProvider":
        _require(printer, "bambu_host", "bambu_serial", "bambu_access_code")
        return cls(
            host=printer.bambu_host,
            serial=printer.bambu_serial,
            access_code=printer.bambu_access_code,
        )

    def _mqtt_client(self) -> mqtt.Client:
        client = (
            self._mqtt_client_factory()
            if self._mqtt_client_factory is not None
            else mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        )
        client.username_pw_set("bblp", self.access_code)
        context = ssl.create_default_context()
        context.load_verify_locations(cadata=_BAMBU_CA_CERTIFICATES)
        context.check_hostname = False
        client.tls_set_context(context)
        # MQTT connects to a LAN IP, while Bambu certificates identify the
        # printer by serial. _validate_mqtt_peer restores that identity check
        # immediately after the verified TLS handshake.
        client.tls_insecure_set(True)
        return client

    def _validate_mqtt_peer(self, client: mqtt.Client) -> None:
        """Require the verified Bambu certificate to belong to this printer."""
        if not hasattr(client, "socket"):  # Contract fake; real paho has it.
            return
        peer_socket = client.socket()
        if peer_socket is None:
            raise ProviderError(
                "bambu MQTT TLS socket unavailable", code="provider_transport_error"
            )
        certificate = peer_socket.getpeercert()
        subjects = certificate.get("subject", ()) if isinstance(certificate, dict) else ()
        common_names = {
            value
            for subject in subjects
            for key, value in subject
            if key == "commonName"
        }
        if self.serial not in common_names:
            raise ProviderError(
                "bambu MQTT certificate identity mismatch",
                code="provider_authentication_failed",
            )

    def _mqtt_request(
        self,
        payload: dict[str, Any],
        *,
        accepts: Callable[[dict[str, Any]], bool],
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        response: dict[str, Any] = {}
        connected = threading.Event()
        received = threading.Event()
        connection_error: list[str] = []

        def on_connect(client, _userdata, _flags, reason_code, _properties=None):  # noqa: ANN001
            if int(reason_code) != 0:
                connection_error.append(f"mqtt connection refused: {reason_code}")
                connected.set()
                return
            try:
                self._validate_mqtt_peer(client)
                client.subscribe(self._report_topic, qos=1)
            except ProviderError as exc:
                connection_error.append(exc.detail)
            connected.set()

        def on_message(_client, _userdata, message):  # noqa: ANN001
            try:
                body = json.loads(message.payload.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return
            if isinstance(body, dict) and accepts(body):
                response.update(body)
                received.set()

        client = self._mqtt_client()
        client.on_connect = on_connect
        client.on_message = on_message
        try:
            client.connect(self.host, 8883, keepalive=30)
            client.loop_start()
            if not connected.wait(timeout):
                raise ProviderError(
                    "bambu_mqtt_connect_timeout", code="provider_timeout"
                )
            if connection_error:
                raise ProviderError(
                    connection_error[0], code="provider_authentication_failed"
                )
            info = client.publish(
                self._request_topic, json.dumps(payload), qos=1, retain=False
            )
            info.wait_for_publish(timeout=timeout)
            if not info.is_published():
                raise ProviderError(
                    "bambu_command_not_published", code="provider_transport_error"
                )
            if not received.wait(timeout):
                raise ProviderError("bambu_response_timeout", code="provider_timeout")
            return response
        finally:
            client.loop_stop()
            client.disconnect()

    async def _send_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        request = payload.get("print", {})
        sequence_id = str(request.get("sequence_id", ""))
        command = request.get("command")

        def accepts(body: dict[str, Any]) -> bool:
            report = body.get("print")
            return bool(
                isinstance(report, dict)
                and report.get("command") == command
                and str(report.get("sequence_id", "")) == sequence_id
                and "result" in report
            )

        try:
            response = await asyncio.to_thread(
                self._mqtt_request, payload, accepts=accepts
            )
            report = response.get("print", {})
            if str(report.get("result", "")).lower() != "success":
                reason = report.get("reason") or "unknown reason"
                raise ProviderError(
                    f"bambu command rejected by printer: {reason}",
                    code="provider_command_rejected",
                )
            return {"ok": True}
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    def _upload_via_ftps(self, local_path: Path, remote_filename: str) -> None:
        """Store a plain-text G-code file in Bambu's cache over implicit FTPS."""

        remote_name = Path(remote_filename).name
        if not remote_name or remote_name != remote_filename:
            raise ProviderError("invalid_bambu_remote_filename", code="provider_error")
        temp_name = f".{remote_name}.{uuid4().hex}.uploading"
        ftp = self._ftps_client()
        try:
            ftp.connect(self.host, 990)
            ftp.login("bblp", self.access_code)
            ftp.prot_p()
            with local_path.open("rb") as source:
                ftp.storbinary(f"STOR cache/{temp_name}", source)
            remote_size = ftp.size(f"cache/{temp_name}")
            if remote_size is not None and remote_size != local_path.stat().st_size:
                raise ProviderError("bambu_upload_size_mismatch", code="provider_error")
            ftp.rename(f"cache/{temp_name}", f"cache/{remote_name}")
        finally:
            try:
                ftp.quit()
            except Exception:  # noqa: BLE001 - connection can fail before greeting
                try:
                    ftp.close()
                except Exception:  # noqa: BLE001 - best effort socket cleanup
                    pass

    @staticmethod
    def _ftps_client() -> FTP_TLS:
        context = ssl.create_default_context()
        # Bambu LAN devices expose a device-local self-signed certificate.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return _ImplicitFTP_TLS(context=context, timeout=30)

    async def upload(self, local_path: Path, remote_filename: str) -> dict[str, Any]:
        self._check("upload")
        try:
            await asyncio.to_thread(self._upload_via_ftps, local_path, remote_filename)
            return {"ok": True, "remote_filename": remote_filename}
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    # Bambu's print.gcode_state uses its own vocabulary (RUNNING/PAUSE/FINISH/
    # …). The rest of the pipeline — the coarse status map *and* the PrintJob
    # lifecycle sync — speaks Moonraker's print_stats.state terms, so translate
    # here. Without this, a paused or finished Bambu print read as UNKNOWN and
    # its job never transitioned to PAUSED/COMPLETED.
    _STATE_TO_MOONRAKER = {
        "idle": "standby",
        "prepare": "standby",
        "slicing": "standby",
        "running": "printing",
        "pause": "paused",
        "finish": "complete",
        "failed": "error",
    }

    def _normalize_status(self, report: dict[str, Any]) -> dict[str, Any]:
        print_report = report.get("print", {})
        raw_state = str(print_report.get("gcode_state", "")).lower()
        gcode_state = self._STATE_TO_MOONRAKER.get(raw_state, raw_state)
        progress = float(print_report.get("mc_percent", 0.0) or 0.0) / 100.0
        filename = print_report.get("subtask_name") or print_report.get("project_id")
        return {
            "print_stats": {
                "state": gcode_state,
                "filename": filename,
                "message": print_report.get("print_error") or "",
            },
            "virtual_sdcard": {
                "progress": max(0.0, min(1.0, progress)),
            },
        }

    async def info(self) -> dict[str, Any]:
        return {
            "result": {
                "provider": "bambu_lan",
                "host": self.host,
                "serial": self.serial,
            }
        }

    async def query_status(self) -> dict[str, Any]:
        self._check("query_status")
        payload = {
            "pushing": {
                "sequence_id": uuid4().hex,
                "command": "pushall",
                "version": 1,
                "push_target": 1,
            }
        }
        try:
            body = await asyncio.to_thread(
                self._mqtt_request,
                payload,
                accepts=lambda report: isinstance(report.get("print"), dict),
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc
        return {"result": {"status": self._normalize_status(body)}}

    async def start(self, remote_filename: str) -> dict[str, Any]:
        self._check("start")
        remote_name = Path(remote_filename).name
        if not remote_name or remote_name != remote_filename:
            raise ProviderError("invalid_bambu_remote_filename", code="provider_error")
        return await self._send_command(
            {
                "print": {
                    "sequence_id": uuid4().hex,
                    "command": "gcode_file",
                    "param": f"/cache/{remote_name}",
                }
            }
        )

    async def pause(self) -> dict[str, Any]:
        self._check("pause")
        return await self._send_command(
            {"print": {"sequence_id": "0", "command": "pause"}}
        )

    async def resume(self) -> dict[str, Any]:
        self._check("resume")
        return await self._send_command(
            {"print": {"sequence_id": "0", "command": "resume"}}
        )

    async def cancel(self) -> dict[str, Any]:
        self._check("cancel")
        return await self._send_command(
            {"print": {"sequence_id": "0", "command": "stop"}}
        )

    async def subscribe_status(
        self,
        on_status: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        if stop_event is not None and stop_event.is_set():
            return
        loop = asyncio.get_running_loop()
        connected = threading.Event()
        first_status = asyncio.Event()
        connection_error: list[str] = []

        def on_connect(client, _userdata, _flags, reason_code, _properties=None):  # noqa: ANN001
            if int(reason_code) != 0:
                connection_error.append(f"mqtt connection refused: {reason_code}")
                connected.set()
                return
            try:
                self._validate_mqtt_peer(client)
                client.subscribe(self._report_topic, qos=1)
                payload = {
                    "pushing": {
                        "sequence_id": uuid4().hex,
                        "command": "pushall",
                        "version": 1,
                        "push_target": 1,
                    }
                }
                client.publish(
                    self._request_topic, json.dumps(payload), qos=1, retain=False
                )
            except ProviderError as exc:
                connection_error.append(exc.detail)
            connected.set()

        def on_message(_client, _userdata, message):  # noqa: ANN001
            try:
                body = json.loads(message.payload.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return
            if not isinstance(body.get("print"), dict):
                return
            future = asyncio.run_coroutine_threadsafe(
                on_status(self._normalize_status(body)), loop
            )
            future.add_done_callback(
                lambda result: loop.call_soon_threadsafe(first_status.set)
                if result.exception() is None
                else logger.warning("bambu status callback failed: %s", result.exception())
            )

        client = self._mqtt_client()
        client.on_connect = on_connect
        client.on_message = on_message
        try:
            client.connect(self.host, 8883, keepalive=30)
            client.loop_start()
            if not await asyncio.to_thread(connected.wait, 10.0):
                raise ProviderError("bambu_mqtt_connect_timeout", code="provider_timeout")
            if connection_error:
                raise ProviderError(
                    connection_error[0], code="provider_authentication_failed"
                )
            if stop_event is None:
                await asyncio.wait_for(first_status.wait(), timeout=10.0)
                return
            await stop_event.wait()
        finally:
            client.loop_stop()
            client.disconnect()


@register
class PrusaLinkProvider(DelegatingProvider):
    provider = PrinterProvider.PRUSALINK
    capabilities = ProviderCapabilities(
        supported=frozenset(
            {
                Capability.START,
                Capability.PAUSE,
                Capability.RESUME,
                Capability.CANCEL,
                Capability.LIVE_STATUS,
                Capability.UPLOAD,
                Capability.LIST_FILES,
                Capability.DELETE_FILE,
                Capability.SERVER_INFO,
            }
        ),
        support_level="beta",
        support_notes=(
            "PrusaLink local FDM support is beta pending broader hardware validation.",
            "Raw G-code controls and measured filament consumption are unavailable.",
        ),
    )
    client_error = PrusaLinkError

    @classmethod
    def build(cls, printer: Printer) -> "PrusaLinkProvider":
        _require(printer, "prusalink_url", "prusalink_auth_mode")
        if printer.prusalink_auth_mode == "digest":
            _require(printer, "prusalink_username", "prusalink_password")
        if printer.prusalink_auth_mode == "api_key":
            _require(printer, "prusalink_api_key")
        return cls(
            PrusaLinkClient(
                printer.prusalink_url,
                auth_mode=printer.prusalink_auth_mode,
                username=printer.prusalink_username,
                password=printer.prusalink_password,
                api_key=printer.prusalink_api_key,
            )
        )


@register
class OctoPrintProvider(DelegatingProvider):
    provider = PrinterProvider.OCTOPRINT
    capabilities = ProviderCapabilities(
        supported=frozenset(
            {
                Capability.START,
                Capability.PAUSE,
                Capability.RESUME,
                Capability.CANCEL,
                Capability.LIVE_STATUS,
                Capability.UPLOAD,
                Capability.LIST_FILES,
                Capability.DELETE_FILE,
                Capability.SERVER_INFO,
            }
        ),
        support_level="beta",
        support_notes=(
            "OctoPrint support is beta pending broader hardware validation.",
            "Raw G-code controls and measured filament consumption are unavailable.",
        ),
    )
    client_error = OctoPrintError

    @classmethod
    def build(cls, printer: Printer) -> "OctoPrintProvider":
        _require(printer, "octoprint_url", "octoprint_api_key")
        return cls(
            OctoPrintClient(printer.octoprint_url, api_key=printer.octoprint_api_key)
        )


@register
class ElegooCentauriProvider(DelegatingProvider):
    provider = PrinterProvider.ELEGOO_CENTAURI
    capabilities = ProviderCapabilities(
        supported=frozenset(
            {
                Capability.START,
                Capability.PAUSE,
                Capability.RESUME,
                Capability.CANCEL,
                Capability.LIVE_STATUS,
                Capability.SERVER_INFO,
                Capability.UPLOAD,
            }
        ),
        support_level="beta",
        support_notes=(
            "Centauri Carbon uses local SDCP; Carbon 2 uses local authenticated MQTT.",
            "Upload runs over plain HTTP, independent of the SDCP/MQTT control channel.",
            "File inventory, deletion, and print-history import remain unavailable.",
        ),
    )
    client_error = ElegooCentauriError

    _VARIANTS = {"elegoo_centauri_carbon", "elegoo_centauri_carbon_2"}

    @classmethod
    def build(cls, printer: Printer) -> "ElegooCentauriProvider":
        _require(printer, "elegoo_centauri_host")
        if printer.provider_variant not in cls._VARIANTS:
            raise ProviderError(
                "provider_credentials_missing", code="provider_credentials_missing"
            )
        if printer.provider_variant == "elegoo_centauri_carbon_2":
            _require(printer, "elegoo_centauri_access_code")
        return cls(
            ElegooCentauriClient(
                printer.elegoo_centauri_host,
                model=printer.provider_variant,
                access_code=printer.elegoo_centauri_access_code,
                mainboard_id=printer.elegoo_centauri_mainboard_id,
            )
        )


def capabilities_for_provider(provider: PrinterProvider) -> ProviderCapabilities:
    return PROVIDERS[provider].capabilities


# Bambu serial number prefix -> friendly model name. Community-sourced
# (Bambu doesn't publish this mapping); best-effort only. An unrecognized
# prefix or a wrong guess is not fatal — the printer's model field is always
# user-editable and a manual value always wins over this detection.
_BAMBU_SERIAL_MODEL_PREFIXES: dict[str, str] = {
    "00M": "Bambu Lab P1P",
    "01S": "Bambu Lab X1",
    "01P": "Bambu Lab X1 Carbon",
    "030": "Bambu Lab A1 mini",
    "039": "Bambu Lab A1",
}

_PROVIDER_VARIANT_MODEL_NAMES: dict[str, str] = {
    "elegoo_neptune4": "Elegoo Neptune 4 family",
    "elegoo_centauri_carbon": "Elegoo Centauri Carbon",
    "elegoo_centauri_carbon_2": "Elegoo Centauri Carbon 2",
}


def detect_printer_model(printer: Printer) -> Optional[str]:
    """Best-effort hardware model name from data already on the printer row.

    No network calls — this only reads fields the user already supplied
    (provider_variant, bambu_serial), so it's free to recompute on every
    create/update. Returns None when nothing is knowable (e.g. plain
    Moonraker/Klipper, which is DIY hardware with no reliable model field);
    callers should let the user set the model manually in that case.
    """
    if printer.provider_variant in _PROVIDER_VARIANT_MODEL_NAMES:
        return _PROVIDER_VARIANT_MODEL_NAMES[printer.provider_variant]
    if printer.provider == PrinterProvider.BAMBU_LAN and printer.bambu_serial:
        return _BAMBU_SERIAL_MODEL_PREFIXES.get(printer.bambu_serial[:3].upper())
    return None


def provider_diagnostic_summary(provider: PrinterProvider) -> dict[str, object]:
    caps = capabilities_for_provider(provider)
    return {
        "provider": provider.value,
        "support_level": caps.support_level,
        "capabilities": caps.action_flags(),
        "unsupported_actions": list(caps.unsupported_actions),
        "notes": list(caps.support_notes),
    }


def get_provider_client(printer: Printer) -> PrinterProviderClient:
    return PROVIDERS[printer.provider].build(printer)
