"""Product-facing adapter for the framework-neutral Bambu LAN client."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from printstash_core.printers.bambu import BambuClient
from printstash_core.printers.models import BambuConfig

from app.db.models import Printer, PrinterProvider


class BambuLanProvider(BambuClient):
    """Map product printer records onto the shared Bambu transport."""

    provider = PrinterProvider.BAMBU_LAN
    capabilities = BambuClient.capabilities

    def __init__(
        self,
        host: str,
        serial: str,
        access_code: str,
        *,
        mqtt_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        super().__init__(
            BambuConfig(host=host, serial=serial, access_code=access_code),
            mqtt_client_factory=mqtt_client_factory,
        )

    @staticmethod
    def config_from_model(printer: Printer) -> BambuConfig:
        return BambuConfig(
            host=printer.bambu_host or "",
            serial=printer.bambu_serial or "",
            access_code=printer.bambu_access_code or "",
        )

    @classmethod
    def from_config(cls, config: BambuConfig) -> BambuLanProvider:
        return cls(config.host, config.serial, config.access_code)

    @classmethod
    def build(cls, printer: Printer) -> BambuLanProvider:
        config = cls.config_from_model(printer)
        instance = cls.__new__(cls)
        BambuClient.__init__(instance, config)
        return instance

    def _ftps_client(self: BambuLanProvider | None = None) -> Any:
        """Retain the historical instance and class-level test seam."""

        if self is not None:
            return BambuClient._ftps_client(self)
        compatibility_client = BambuClient(
            BambuConfig(host="localhost", serial="compat", access_code="compat")
        )
        return compatibility_client._ftps_client()
