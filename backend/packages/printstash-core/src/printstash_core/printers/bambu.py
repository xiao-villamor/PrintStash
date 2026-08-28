"""Framework-neutral Bambu LAN client using MQTT and implicit FTPS."""

from __future__ import annotations

import asyncio
import errno
import json
import logging
import socket
import ssl
import threading
from collections.abc import Awaitable, Callable, Mapping
from ftplib import FTP_TLS, error_perm, error_reply, error_temp
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from uuid import uuid4

from .contracts import PrinterClient, SnapshotCallback
from .models import (
    BambuConfig,
    Capability,
    PrinterConfig,
    PrinterSnapshot,
    ProviderCapabilities,
    ProviderError,
    ProviderId,
)

LegacyStatusCallback = Callable[[dict[str, Any]], Awaitable[None]]

# MQTT connects by LAN address while the leaf certificate identifies the
# printer by serial. The Bambu CA chain verifies the handshake; the serial is
# checked explicitly immediately after connection.
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
    """``ftplib`` variant for Bambu's implicit-TLS port 990."""

    def connect(
        self,
        host: str = "",
        port: int = 0,
        timeout: Any = -999,
        source_address: tuple[str, int] | None = None,
    ) -> str:
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


class BambuClient:
    """Bambu LAN transport constructed solely from :class:`BambuConfig`."""

    capabilities = ProviderCapabilities(
        supported=frozenset(
            {
                Capability.START,
                Capability.PAUSE,
                Capability.RESUME,
                Capability.CANCEL,
                Capability.LIVE_STATUS,
                Capability.UPLOAD,
                Capability.MATERIAL_STATE,
            }
        ),
        support_level="beta",
        support_notes=(
            "Bambu LAN upload and explicit start are beta features.",
            "Printer file inventory, deletion, raw G-code controls, and measured filament consumption are unavailable.",
        ),
        requires_ready_before_send=True,
    )

    _METHOD_CAPABILITY = {
        "server_info": Capability.SERVER_INFO,
        "server_config": Capability.SERVER_CONFIG,
        "printer_config": Capability.PRINTER_CONFIG,
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
    }
    _STATE_TO_MOONRAKER = {
        "idle": "standby",
        "prepare": "standby",
        "slicing": "standby",
        "running": "printing",
        "pause": "paused",
        "finish": "complete",
        "failed": "error",
    }
    _FTPS_RETRYABLE = frozenset(
        {
            "bambu_ftps_transport_error",
            "bambu_ftps_connection_reset",
            "bambu_ftps_timeout",
            "bambu_ftps_eof",
        }
    )

    def __init__(
        self,
        config: BambuConfig,
        *,
        mqtt_client_factory: Callable[[], Any] | None = None,
        ftps_client_factory: Callable[[], FTP_TLS] | None = None,
        sequence_id_factory: Callable[[], str] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.host = config.host
        self.serial = config.serial
        self.access_code = config.access_code
        self._mqtt_client_factory = mqtt_client_factory
        self._ftps_client_factory = ftps_client_factory
        self._sequence_id_factory = sequence_id_factory
        self._logger = logger or logging.getLogger(__name__)
        self._request_topic = f"device/{self.serial}/request"
        self._report_topic = f"device/{self.serial}/report"
        self._material_slots: dict[str, dict[str, Any]] = {}

    def _next_sequence_id(self) -> str:
        if self._sequence_id_factory is not None:
            return self._sequence_id_factory()
        return uuid4().hex

    @staticmethod
    def _default_mqtt_client() -> Any:
        try:
            mqtt = import_module("paho.mqtt.client")
        except ModuleNotFoundError:
            raise ProviderError(
                "bambu_mqtt_dependency_missing", code="provider_dependency_missing"
            ) from None
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)

    def _mqtt_client(self) -> Any:
        client = (
            self._mqtt_client_factory()
            if self._mqtt_client_factory is not None
            else self._default_mqtt_client()
        )
        client.username_pw_set("bblp", self.access_code)
        context = ssl.create_default_context()
        context.load_verify_locations(cadata=_BAMBU_CA_CERTIFICATES)
        context.check_hostname = False
        client.tls_set_context(context)
        # Paho would otherwise compare the LAN IP to the serial-number CN.
        # _validate_mqtt_peer performs that identity check after the handshake.
        client.tls_insecure_set(True)
        return client

    def _validate_mqtt_peer(self, client: Any) -> None:
        """Require the verified Bambu certificate to belong to this printer."""

        if not hasattr(client, "socket"):
            return
        peer_socket = client.socket()
        if peer_socket is None:
            raise ProviderError(
                "bambu MQTT TLS socket unavailable", code="provider_transport_error"
            )
        certificate = peer_socket.getpeercert()
        subjects = (
            certificate.get("subject", ()) if isinstance(certificate, dict) else ()
        )
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

    @staticmethod
    def _connection_failed(reason_code: object) -> bool:
        is_failure = getattr(reason_code, "is_failure", None)
        if isinstance(is_failure, bool):
            return is_failure
        return reason_code != 0

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

        def on_connect(
            client: Any,
            _userdata: Any,
            _flags: Any,
            reason_code: object,
            _properties: Any = None,
        ) -> None:
            if self._connection_failed(reason_code):
                connection_error.append(f"mqtt connection refused: {reason_code}")
                connected.set()
                return
            try:
                self._validate_mqtt_peer(client)
                client.subscribe(self._report_topic, qos=1)
            except ProviderError as exc:
                connection_error.append(exc.detail)
            connected.set()

        def on_message(_client: Any, _userdata: Any, message: Any) -> None:
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
            publish_rc = getattr(info, "rc", 0)
            if isinstance(publish_rc, int) and publish_rc != 0:
                raise ProviderError(
                    "bambu_command_not_published", code="provider_transport_error"
                )
            if not received.wait(timeout):
                raise ProviderError("bambu_response_timeout", code="provider_timeout")
            return response
        finally:
            try:
                client.disconnect()
            finally:
                client.loop_stop()

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

    def _ftps_client(self) -> FTP_TLS:
        if self._ftps_client_factory is not None:
            return self._ftps_client_factory()
        context = ssl.create_default_context()
        # Bambu FTPS exposes a device-local self-signed certificate.
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return _ImplicitFTP_TLS(context=context, timeout=30)

    @staticmethod
    def _ftps_error(
        detail: str, *, action_code: str, retryable: bool = False
    ) -> ProviderError:
        # ``code`` remains the coarse compatibility category.  The actionable
        # value is persisted and exposed by the application as ``action_code``.
        return ProviderError(
            detail,
            # FTPS login failures are transport/authentication failures at the
            # provider seam. Keep the coarse compatibility code expected by
            # the API while retaining the precise actionable reason below.
            code=(
                "provider_transport_error"
                if action_code == "bambu_ftps_authentication_failed"
                else "provider_error"
            ),
            action_code=action_code,
            retryable=retryable,
        )

    @staticmethod
    def _is_ftps_authentication_response(
        response: str, *, allow_auth_keyword: bool = True
    ) -> bool:
        """Recognize FTP authentication replies from transport errors."""

        normalized = response.lower().strip()
        return normalized.startswith("530") or (
            allow_auth_keyword and "auth" in normalized
        )

    @classmethod
    def _classify_ftps_exception(cls, exc: BaseException) -> ProviderError:
        if isinstance(exc, ProviderError):
            return exc
        retryable = False
        if isinstance(exc, ssl.SSLError):
            code = "bambu_ftps_tls_error"
        elif isinstance(exc, (socket.timeout, TimeoutError)):
            code = "bambu_ftps_timeout"
            retryable = True
        elif isinstance(exc, ConnectionResetError):
            code = "bambu_ftps_connection_reset"
            retryable = True
        elif isinstance(exc, EOFError):
            code = "bambu_ftps_eof"
            retryable = True
        elif isinstance(exc, (error_perm, error_reply, error_temp)):
            response = str(exc).lower()
            if cls._is_ftps_authentication_response(response):
                code = "bambu_ftps_authentication_failed"
            elif response.startswith("552"):
                code = "bambu_ftps_too_large"
            elif response.startswith("450"):
                code = "bambu_ftps_not_found"
            elif response.startswith("550") or "not found" in response:
                code = "bambu_ftps_not_found"
            elif response.startswith("553") or "path" in response:
                code = "bambu_ftps_path_invalid"
            else:
                # Unknown FTP replies are server decisions, not evidence of
                # a transient transport failure. Persist a stable code and
                # leave the one-shot retry reserved for explicit transport
                # outcomes only.
                code = "bambu_ftps_server_rejected"
        elif isinstance(exc, OSError) and cls._is_ftps_authentication_response(
            str(exc), allow_auth_keyword=False
        ):
            # Some FTPS implementations (including the integration fake) wrap
            # a 530 login reply in PermissionError instead of ftplib.error_perm.
            code = "bambu_ftps_authentication_failed"
        elif isinstance(exc, (ConnectionError, socket.gaierror)) or (
            isinstance(exc, OSError)
            and getattr(exc, "errno", None)
            in {
                getattr(errno, name)
                for name in (
                    "ECONNABORTED",
                    "ECONNREFUSED",
                    "ECONNRESET",
                    "EHOSTDOWN",
                    "EHOSTUNREACH",
                    "ENETDOWN",
                    "ENETRESET",
                    "ENETUNREACH",
                    "EPIPE",
                    "ETIMEDOUT",
                )
                if hasattr(errno, name)
            }
        ):
            code = "bambu_ftps_transport_error"
            retryable = True
        elif isinstance(exc, OSError):
            # File/path/permission failures happen on the local side of the
            # transfer and are never made transient by replaying the upload.
            code = "bambu_ftps_local_error"
        else:
            code = "bambu_ftps_unknown_error"
        return cls._ftps_error(code, action_code=code, retryable=retryable)

    @classmethod
    def _with_ftps_retry(cls, operation: Callable[[], None]) -> None:
        """Replay exactly once when the transport outcome is safely retryable."""

        for attempt in range(2):
            try:
                operation()
                return
            except Exception as exc:  # noqa: BLE001 - classify at the seam
                classified = cls._classify_ftps_exception(exc)
                if (
                    attempt == 0
                    and classified.retryable
                    and classified.action_code in cls._FTPS_RETRYABLE
                ):
                    continue
                raise classified from exc

    @staticmethod
    def _close_ftps(ftp: FTP_TLS) -> None:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001 - connection can fail before greeting
            try:
                ftp.close()
            except Exception:  # noqa: BLE001 - best-effort socket cleanup
                pass

    def _upload_once(self, local_path: Path, remote_name: str) -> None:
        temp_name = f".{remote_name}.{self._next_sequence_id()}.uploading"
        ftp = self._ftps_client()
        try:
            ftp.connect(self.host, 990)
            ftp.login("bblp", self.access_code)
            ftp.prot_p()
            with local_path.open("rb") as source:
                ftp.storbinary(f"STOR cache/{temp_name}", source)
            remote_size = ftp.size(f"cache/{temp_name}")
            if remote_size is not None and remote_size != local_path.stat().st_size:
                raise self._ftps_error(
                    "bambu_upload_size_mismatch",
                    action_code="bambu_ftps_size_mismatch",
                )
            ftp.rename(f"cache/{temp_name}", f"cache/{remote_name}")
        finally:
            self._close_ftps(ftp)

    def _upload_via_ftps(self, local_path: Path, remote_filename: str) -> None:
        """Store a plain-text G-code file in Bambu's cache over implicit FTPS."""

        remote_name = Path(remote_filename).name
        if not remote_name or remote_name != remote_filename:
            raise self._ftps_error(
                "invalid_bambu_remote_filename",
                action_code="bambu_ftps_path_invalid",
            )
        self._with_ftps_retry(lambda: self._upload_once(local_path, remote_name))

    def _download_once(
        self, remote_name: str, local_path: Path, *, max_bytes: int
    ) -> None:
        ftp = self._ftps_client()
        written = 0
        try:
            ftp.connect(self.host, 990)
            ftp.login("bblp", self.access_code)
            ftp.prot_p()
            remote_size = ftp.size(remote_name)
            if remote_size is not None and remote_size > max_bytes:
                raise self._ftps_error(
                    "bambu_artifact_too_large", action_code="bambu_ftps_too_large"
                )
            with local_path.open("wb") as destination:

                def write_chunk(chunk: bytes) -> None:
                    nonlocal written
                    written += len(chunk)
                    if written > max_bytes:
                        raise self._ftps_error(
                            "bambu_artifact_too_large",
                            action_code="bambu_ftps_too_large",
                        )
                    destination.write(chunk)

                ftp.retrbinary(f"RETR {remote_name}", write_chunk)
            if remote_size is not None and written != remote_size:
                raise self._ftps_error(
                    "bambu_download_size_mismatch",
                    action_code="bambu_ftps_size_mismatch",
                )
        finally:
            self._close_ftps(ftp)

    def _download_via_ftps(
        self, remote_path: str, local_path: Path, *, max_bytes: int
    ) -> None:
        """Recover a cached G-code/project archive without trusting MQTT paths."""

        parsed = urlparse(remote_path)
        if parsed.scheme not in ("", "ftp", "ftps"):
            raise self._ftps_error(
                "invalid_bambu_artifact_path", action_code="bambu_ftps_path_invalid"
            )
        # `urlparse` lower-cases the host component, and Bambu reports its own
        # artifact URLs as `ftps://<SERIAL>/cache/...` with the serial in upper
        # case. Comparing case-sensitively refused every real capture URL.
        if parsed.hostname and parsed.hostname.lower() not in {
            self.host.lower(),
            self.serial.lower(),
        }:
            raise self._ftps_error(
                "invalid_bambu_artifact_host", action_code="bambu_ftps_path_invalid"
            )
        raw_path = unquote(parsed.path).replace("\\", "/")
        parts = [part for part in raw_path.split("/") if part]
        if not parts or any(part in (".", "..") for part in parts):
            raise self._ftps_error(
                "invalid_bambu_artifact_path", action_code="bambu_ftps_path_invalid"
            )
        if len(parts) == 1:
            parts.insert(0, "cache")
        if parts[0] != "cache":
            raise self._ftps_error(
                "invalid_bambu_artifact_path", action_code="bambu_ftps_path_invalid"
            )
        remote_name = "/".join(parts)
        if not remote_name.lower().endswith(
            (".gcode", ".g", ".gco", ".bgcode", ".3mf")
        ):
            raise self._ftps_error(
                "unsupported_bambu_artifact", action_code="bambu_ftps_path_invalid"
            )

        self._with_ftps_retry(
            lambda: self._download_once(remote_name, local_path, max_bytes=max_bytes)
        )

    def _check(self, method: str) -> None:
        capability = self._METHOD_CAPABILITY.get(method)
        if capability is not None and not self.capabilities.supports(capability):
            raise ProviderError(
                "operation_not_supported_for_provider",
                code="operation_not_supported_for_provider",
            )

    def _normalize_status(self, report: dict[str, Any]) -> dict[str, Any]:
        print_report = report.get("print", {})
        status: dict[str, Any] = {}
        print_stats: dict[str, Any] = {}
        if print_report.get("gcode_state") not in (None, ""):
            raw_state = str(print_report["gcode_state"]).lower()
            print_stats["state"] = self._STATE_TO_MOONRAKER.get(raw_state, raw_state)
        gcode_file = print_report.get("gcode_file")
        filename = (
            Path(str(gcode_file).replace("\\", "/")).name
            if gcode_file
            else print_report.get("subtask_name") or print_report.get("project_id")
        )
        if filename:
            print_stats["filename"] = str(filename)
        if "print_error" in print_report:
            print_stats["message"] = print_report.get("print_error") or ""
        if print_stats:
            status["print_stats"] = print_stats
        if print_report.get("mc_percent") is not None:
            try:
                progress = float(print_report["mc_percent"]) / 100.0
            except (TypeError, ValueError):
                pass
            else:
                status["virtual_sdcard"] = {"progress": max(0.0, min(1.0, progress))}

        metadata_fields = {
            "subtask_name": "external_display_name",
            "task_id": "external_task_id",
            "subtask_id": "external_subtask_id",
            "project_id": "external_project_id",
            "profile_id": "external_profile_id",
            "gcode_file": "external_gcode_file",
            "plate_num": "external_plate_index",
            "layer_num": "external_current_layer",
            "total_layer_num": "external_total_layers",
            "nozzle_diameter": "external_nozzle_diameter",
        }
        for source, target in metadata_fields.items():
            value = print_report.get(source)
            if value is not None and value != "":
                print_stats[target] = value
        if print_stats:
            status["print_stats"] = print_stats
        if isinstance(print_report, dict) and (
            "ams" in print_report or "vt_tray" in print_report
        ):
            for slot in self._normalize_ams_slots(print_report):
                self._material_slots[str(slot["slot_key"])] = slot
        if self._material_slots:
            status["material_slots"] = list(self._material_slots.values())
        nozzle = print_report.get("nozzle_diameter")
        try:
            nozzle_diameter = float(nozzle) if nozzle is not None else None
        except (TypeError, ValueError):
            nozzle_diameter = None
        if nozzle_diameter is not None and nozzle_diameter > 0:
            status["material_tools"] = [
                {
                    "tool_key": "tool0",
                    "label": "Tool 0",
                    "nozzle_diameter_mm": nozzle_diameter,
                }
            ]
        return status

    @staticmethod
    def _normalize_ams_slots(print_report: dict[str, Any]) -> list[dict[str, Any]]:
        slots: list[dict[str, Any]] = []
        ams_root = print_report.get("ams")
        units = ams_root.get("ams", []) if isinstance(ams_root, dict) else []
        if isinstance(units, list):
            for unit in units:
                if not isinstance(unit, dict):
                    continue
                unit_id = str(unit.get("id", len(slots)))
                trays = unit.get("tray", [])
                if not isinstance(trays, list):
                    continue
                for tray in trays:
                    if not isinstance(tray, dict):
                        continue
                    tray_id = str(tray.get("id", len(slots)))
                    material = str(tray.get("tray_type") or "").strip() or None
                    color = str(tray.get("tray_color") or "").strip().upper()
                    color_hex = f"#{color[:6]}" if len(color) >= 6 else None
                    slots.append(
                        {
                            "slot_key": f"ams:{unit_id}:{tray_id}",
                            "label": f"AMS {unit_id} tray {tray_id}",
                            "tool_key": "tool0",
                            "state": "loaded" if material else "empty",
                            "material_type": material,
                            "color_hex": color_hex,
                        }
                    )
        virtual = print_report.get("vt_tray")
        if isinstance(virtual, dict):
            material = str(virtual.get("tray_type") or "").strip() or None
            color = str(virtual.get("tray_color") or "").strip().upper()
            slots.append(
                {
                    "slot_key": "external",
                    "label": "External spool",
                    "tool_key": "tool0",
                    "state": "loaded" if material else "empty",
                    "material_type": material,
                    "color_hex": f"#{color[:6]}" if len(color) >= 6 else None,
                }
            )
        return slots

    def _normalize_project_request(self, report: dict[str, Any]) -> dict[str, Any]:
        """Extract a best-effort FTPS capture hint from project_file traffic."""

        print_request = report.get("print", {})
        if (
            not isinstance(print_request, dict)
            or print_request.get("command") != "project_file"
        ):
            return {}
        candidate = (
            print_request.get("url")
            or print_request.get("file")
            or print_request.get("gcode_file")
        )
        normalized = self._normalize_status(report)
        if candidate:
            normalized.setdefault("print_stats", {})["external_artifact_path"] = str(
                candidate
            )
        return normalized

    async def info(self) -> dict[str, Any]:
        return {
            "result": {
                "provider": "bambu_lan",
                "host": self.host,
                "serial": self.serial,
            }
        }

    async def server_info(self) -> dict[str, Any]:
        self._check("server_info")
        raise NotImplementedError

    async def server_config(self) -> dict[str, Any]:
        self._check("server_config")
        raise NotImplementedError

    async def printer_config(self) -> dict[str, Any]:
        self._check("printer_config")
        raise NotImplementedError

    async def query_status(self) -> dict[str, Any]:
        """Return the existing provider result envelope unchanged."""

        self._check("query_status")
        payload = {
            "pushing": {
                "sequence_id": self._next_sequence_id(),
                "command": "pushall",
                "version": 1,
                "push_target": 1,
            }
        }
        try:
            body = await asyncio.to_thread(
                self._mqtt_request,
                payload,
                accepts=lambda report: bool(
                    isinstance(report.get("print"), dict)
                    and {
                        "gcode_state",
                        "mc_percent",
                        "subtask_name",
                        "project_id",
                        "gcode_file",
                        "print_error",
                        "ams",
                        "vt_tray",
                    }.intersection(report["print"])
                ),
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc
        return {"result": {"status": self._normalize_status(body)}}

    async def query_snapshot(self) -> PrinterSnapshot:
        return PrinterSnapshot.from_legacy_payload(await self.query_status())

    async def list_files(self) -> list[Mapping[str, Any]]:
        self._check("list_files")
        raise NotImplementedError

    async def upload(self, local_path: Path, remote_filename: str) -> dict[str, Any]:
        self._check("upload")
        try:
            await asyncio.to_thread(self._upload_via_ftps, local_path, remote_filename)
            return {"ok": True, "remote_filename": remote_filename}
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    async def download_artifact(
        self, remote_path: str, destination: Path, *, max_bytes: int
    ) -> None:
        try:
            await asyncio.to_thread(
                self._download_via_ftps,
                remote_path,
                destination,
                max_bytes=max_bytes,
            )
        except ProviderError:
            destination.unlink(missing_ok=True)
            raise
        except Exception as exc:
            destination.unlink(missing_ok=True)
            raise ProviderError(str(exc), code="provider_transport_error") from exc

    async def delete_file(self, remote_filename: str) -> dict[str, Any]:
        self._check("delete_file")
        raise NotImplementedError

    async def start(self, remote_filename: str) -> dict[str, Any]:
        self._check("start")
        remote_name = Path(remote_filename).name
        if not remote_name or remote_name != remote_filename:
            raise ProviderError("invalid_bambu_remote_filename", code="provider_error")
        return await self._send_command(
            {
                "print": {
                    "sequence_id": self._next_sequence_id(),
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

    async def run_gcode(self, script: str) -> dict[str, Any]:
        self._check("run_gcode")
        raise NotImplementedError

    async def emergency_stop(self) -> dict[str, Any]:
        self._check("emergency_stop")
        raise NotImplementedError

    async def subscribe_status(
        self,
        on_status: LegacyStatusCallback,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        """Subscribe with the existing direct-status callback shape."""

        if stop_event is not None and stop_event.is_set():
            return
        loop = asyncio.get_running_loop()
        connected = asyncio.Event()
        first_status = asyncio.Event()
        connection_error: list[str] = []

        async def dispatch(status: dict[str, Any]) -> None:
            await on_status(status)

        def on_connect(
            client: Any,
            _userdata: Any,
            _flags: Any,
            reason_code: object,
            _properties: Any = None,
        ) -> None:
            if self._connection_failed(reason_code):
                connection_error.append(f"mqtt connection refused: {reason_code}")
                loop.call_soon_threadsafe(connected.set)
                return
            try:
                self._validate_mqtt_peer(client)
                client.subscribe(self._report_topic, qos=1)
                payload = {
                    "pushing": {
                        "sequence_id": self._next_sequence_id(),
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
            loop.call_soon_threadsafe(connected.set)

        def on_message(_client: Any, _userdata: Any, message: Any) -> None:
            try:
                body = json.loads(message.payload.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return
            if not isinstance(body.get("print"), dict):
                return
            # Bambu can put project_file capture hints on the report topic,
            # which is the only topic this persistent session subscribes to.
            # Prefer that shape regardless of topic, then fall back to the
            # ordinary status normalizer for all other printer reports.
            normalized = self._normalize_project_request(body)
            if not normalized:
                normalized = self._normalize_status(body)
            if not normalized:
                return
            future = asyncio.run_coroutine_threadsafe(dispatch(normalized), loop)
            future.add_done_callback(
                lambda result: (
                    loop.call_soon_threadsafe(first_status.set)
                    if result.exception() is None
                    else self._logger.warning(
                        "bambu status callback failed: %s", result.exception()
                    )
                )
            )

        client = self._mqtt_client()
        client.on_connect = on_connect
        client.on_message = on_message
        try:
            client.connect(self.host, 8883, keepalive=30)
            client.loop_start()
            try:
                await asyncio.wait_for(connected.wait(), timeout=10.0)
            except TimeoutError:
                raise ProviderError(
                    "bambu_mqtt_connect_timeout", code="provider_timeout"
                ) from None
            if connection_error:
                raise ProviderError(
                    connection_error[0], code="provider_authentication_failed"
                )
            if stop_event is None:
                await asyncio.wait_for(first_status.wait(), timeout=10.0)
                return
            await stop_event.wait()
        finally:
            try:
                client.disconnect()
            finally:
                client.loop_stop()

    async def subscribe_snapshots(
        self,
        on_snapshot: SnapshotCallback,
        *,
        stop_event: asyncio.Event | None = None,
    ) -> None:
        async def adapt(status: dict[str, Any]) -> None:
            await on_snapshot(PrinterSnapshot.from_legacy_payload(status))

        await self.subscribe_status(adapt, stop_event=stop_event)


class BambuFactory:
    """Build Bambu clients while retaining injectable transport seams."""

    provider_id = ProviderId.BAMBU_LAN
    capabilities = BambuClient.capabilities

    def __init__(
        self,
        *,
        mqtt_client_factory: Callable[[], Any] | None = None,
        ftps_client_factory: Callable[[], FTP_TLS] | None = None,
        sequence_id_factory: Callable[[], str] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._mqtt_client_factory = mqtt_client_factory
        self._ftps_client_factory = ftps_client_factory
        self._sequence_id_factory = sequence_id_factory
        self._logger = logger

    def build(self, config: PrinterConfig) -> PrinterClient:
        if not isinstance(config, BambuConfig):
            raise ProviderError(
                "provider_config_mismatch", code="provider_config_mismatch"
            )
        return BambuClient(
            config,
            mqtt_client_factory=self._mqtt_client_factory,
            ftps_client_factory=self._ftps_client_factory,
            sequence_id_factory=self._sequence_id_factory,
            logger=self._logger,
        )
