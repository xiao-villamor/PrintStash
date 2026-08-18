"""Compatibility facade and product composition for printer providers.

Wire protocols live in :mod:`printstash_core.printers`.  This module keeps the
historical application imports stable, maps ORM records to immutable configs,
and applies product-level capability gates.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Protocol, cast, runtime_checkable

from printstash_core.printers import (
    BambuConfig,
    Capability,
    ElegooCentauriConfig,
    MoonrakerConfig,
    OctoPrintConfig,
    PrinterClient,
    PrinterConfig,
    ProviderCapabilities,
    ProviderError,
    ProviderId,
    ProviderRegistry,
    PrusaLinkConfig,
)
from printstash_core.printers.catalog import PROVIDER_DEFINITIONS

from app.db.models import Printer, PrinterProvider
from app.services.bambu_adapter import BambuLanProvider
from app.services.elegoo_centauri import ElegooCentauriClient, ElegooCentauriError
from app.services.moonraker import MoonrakerClient, MoonrakerError
from app.services.octoprint import OctoPrintClient, OctoPrintError
from app.services.prusalink import PrusaLinkClient, PrusaLinkError

# Provider method -> capability required before an adapter may perform I/O.
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


@runtime_checkable
class PrinterProviderClient(Protocol):
    """Legacy Moonraker-shaped surface retained for application consumers."""

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
    if any(not getattr(printer, field, None) for field in fields):
        raise ProviderError(
            "provider_credentials_missing", code="provider_credentials_missing"
        )


class BaseProvider:
    """Capability-gated product adapter with no wire-protocol implementation."""

    provider: PrinterProvider
    capabilities: ProviderCapabilities

    @classmethod
    def build(cls, printer: Printer) -> BaseProvider:  # pragma: no cover - abstract
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
    """Translate a core client's errors while retaining the legacy surface."""

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


PROVIDERS: dict[PrinterProvider, type[Any]] = {}


def register(cls: type[Any]) -> type[Any]:
    PROVIDERS[cls.provider] = cls
    return cls


@register
class MoonrakerProvider(DelegatingProvider):
    provider = PrinterProvider.MOONRAKER
    capabilities = PROVIDER_DEFINITIONS[ProviderId.MOONRAKER].capabilities
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
    def build(cls, printer: Printer) -> MoonrakerProvider:
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


register(BambuLanProvider)


@register
class PrusaLinkProvider(DelegatingProvider):
    provider = PrinterProvider.PRUSALINK
    capabilities = PROVIDER_DEFINITIONS[ProviderId.PRUSALINK].capabilities
    client_error = PrusaLinkError

    @classmethod
    def build(cls, printer: Printer) -> PrusaLinkProvider:
        _require(printer, "prusalink_url", "prusalink_auth_mode")
        if printer.prusalink_auth_mode == "digest":
            _require(printer, "prusalink_username", "prusalink_password")
        if printer.prusalink_auth_mode == "api_key":
            _require(printer, "prusalink_api_key")
        return cls(
            PrusaLinkClient(
                cast(str, printer.prusalink_url),
                auth_mode=cast(str, printer.prusalink_auth_mode),
                username=printer.prusalink_username,
                password=printer.prusalink_password,
                api_key=printer.prusalink_api_key,
            )
        )


@register
class OctoPrintProvider(DelegatingProvider):
    provider = PrinterProvider.OCTOPRINT
    capabilities = PROVIDER_DEFINITIONS[ProviderId.OCTOPRINT].capabilities
    client_error = OctoPrintError

    @classmethod
    def build(cls, printer: Printer) -> OctoPrintProvider:
        _require(printer, "octoprint_url", "octoprint_api_key")
        return cls(
            OctoPrintClient(
                cast(str, printer.octoprint_url),
                api_key=cast(str, printer.octoprint_api_key),
            )
        )


@register
class ElegooCentauriProvider(DelegatingProvider):
    provider = PrinterProvider.ELEGOO_CENTAURI
    capabilities = PROVIDER_DEFINITIONS[ProviderId.ELEGOO_CENTAURI].capabilities
    client_error = ElegooCentauriError
    _VARIANTS = {"elegoo_centauri_carbon", "elegoo_centauri_carbon_2"}

    @classmethod
    def build(cls, printer: Printer) -> ElegooCentauriProvider:
        _require(printer, "elegoo_centauri_host")
        if printer.provider_variant not in cls._VARIANTS:
            raise ProviderError(
                "provider_credentials_missing", code="provider_credentials_missing"
            )
        if printer.provider_variant == "elegoo_centauri_carbon_2":
            _require(printer, "elegoo_centauri_access_code")
        return cls(
            ElegooCentauriClient(
                cast(str, printer.elegoo_centauri_host),
                model=cast(str, printer.provider_variant),
                access_code=printer.elegoo_centauri_access_code,
                mainboard_id=printer.elegoo_centauri_mainboard_id,
            )
        )


def capabilities_for_provider(provider: PrinterProvider) -> ProviderCapabilities:
    return PROVIDERS[provider].capabilities


def printer_config_from_model(printer: Printer) -> PrinterConfig:
    """Copy one ORM record into an immutable, persistence-neutral config."""

    if printer.provider == PrinterProvider.MOONRAKER:
        return MoonrakerConfig(
            base_url=printer.moonraker_url or "",
            api_key=printer.api_key,
            variant=printer.provider_variant,
        )
    if printer.provider == PrinterProvider.BAMBU_LAN:
        return BambuConfig(
            host=printer.bambu_host or "",
            serial=printer.bambu_serial or "",
            access_code=printer.bambu_access_code or "",
        )
    if printer.provider == PrinterProvider.PRUSALINK:
        return PrusaLinkConfig(
            base_url=printer.prusalink_url or "",
            auth_mode=printer.prusalink_auth_mode or "",
            username=printer.prusalink_username,
            password=printer.prusalink_password,
            api_key=printer.prusalink_api_key,
        )
    if printer.provider == PrinterProvider.OCTOPRINT:
        return OctoPrintConfig(
            base_url=printer.octoprint_url or "",
            api_key=printer.octoprint_api_key or "",
        )
    if printer.provider == PrinterProvider.ELEGOO_CENTAURI:
        return ElegooCentauriConfig(
            host=printer.elegoo_centauri_host or "",
            model=printer.provider_variant or "",
            access_code=printer.elegoo_centauri_access_code,
            mainboard_id=printer.elegoo_centauri_mainboard_id,
        )
    raise ProviderError("unknown_provider", code="unknown_provider")


class _ProductProviderFactory:
    """Build compatibility adapters from neutral core configurations."""

    def __init__(self, provider_id: ProviderId, adapter_type: type[Any]) -> None:
        self.provider_id = provider_id
        self._adapter_type = adapter_type
        self.capabilities = cast(ProviderCapabilities, adapter_type.capabilities)

    def build(self, config: PrinterConfig) -> PrinterClient:
        if isinstance(config, MoonrakerConfig):
            adapter = MoonrakerProvider(config.base_url, config.api_key)
        elif isinstance(config, BambuConfig):
            adapter = BambuLanProvider(config.host, config.serial, config.access_code)
        elif isinstance(config, PrusaLinkConfig):
            adapter = PrusaLinkProvider(
                PrusaLinkClient(
                    config.base_url,
                    auth_mode=config.auth_mode,
                    username=config.username,
                    password=config.password,
                    api_key=config.api_key,
                )
            )
        elif isinstance(config, OctoPrintConfig):
            adapter = OctoPrintProvider(
                OctoPrintClient(config.base_url, api_key=config.api_key)
            )
        elif isinstance(config, ElegooCentauriConfig):
            adapter = ElegooCentauriProvider(
                ElegooCentauriClient(
                    config.host,
                    model=config.model,
                    access_code=config.access_code,
                    mainboard_id=config.mainboard_id,
                )
            )
        else:  # pragma: no cover - the union is intentionally exhaustive
            raise ProviderError("unknown_provider", code="unknown_provider")
        return cast(PrinterClient, adapter)


def build_provider_registry() -> ProviderRegistry:
    """Create the product-owned registry used by the composition root."""

    return ProviderRegistry(
        _ProductProviderFactory(ProviderId(provider.value), adapter_type)
        for provider, adapter_type in PROVIDERS.items()
    )


_DEFAULT_PROVIDER_REGISTRY = build_provider_registry()


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
    if printer.provider_variant in _PROVIDER_VARIANT_MODEL_NAMES:
        return _PROVIDER_VARIANT_MODEL_NAMES[printer.provider_variant]
    if printer.provider == PrinterProvider.BAMBU_LAN and printer.bambu_serial:
        return _BAMBU_SERIAL_MODEL_PREFIXES.get(printer.bambu_serial[:3].upper())
    return None


def provider_diagnostic_summary(provider: PrinterProvider) -> dict[str, object]:
    capabilities = capabilities_for_provider(provider)
    return {
        "provider": provider.value,
        "support_level": capabilities.support_level,
        "capabilities": capabilities.action_flags(),
        "unsupported_actions": list(capabilities.unsupported_actions),
        "notes": list(capabilities.support_notes),
    }


def get_provider_client(
    printer: Printer, *, registry: ProviderRegistry | None = None
) -> PrinterProviderClient:
    selected_registry = registry or _DEFAULT_PROVIDER_REGISTRY
    config = printer_config_from_model(printer)
    client = selected_registry.build(config.provider_id, config)
    return cast(PrinterProviderClient, client)
