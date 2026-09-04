"""The seam between an ORM row and a printer client that must not hold onto it.

This adapter turns a `Printer` row into an immutable core config. Two things about
that are worth pinning.

**It must not retain the row.** The client outlives the request and its session; a
held ORM instance becomes a detached object whose attribute access raises much
later, from inside a background dispatch. So `build` copies the fields it needs.

**Credential validation belongs to the core, not here.** Duplicating it would let
the two disagree, and the adapter's copy would be the one that silently accepted a
half-configured printer.

The legacy-argument rows exist because this constructor and its class-level FTPS
factory are still called by older code paths; changing their shape breaks a caller
this file is the only warning for.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from printstash_core.printers.bambu import BambuClient
from printstash_core.printers.models import ProviderError

from app.db.models import PrinterProvider
from app.services.bambu_adapter import BambuLanProvider
from tests.factories import printer_config


class TestBuild:
    def test_build_maps_orm_fields_without_retaining_the_row(self) -> None:
        printer = printer_config(
            "Bambu",
            provider=PrinterProvider.BAMBU_LAN,
            bambu_host="192.0.2.20",
            bambu_serial="SERIAL-2",
            bambu_access_code="code-2",
        )

        provider = BambuLanProvider.build(printer)

        assert provider.config.host == "192.0.2.20"
        assert provider.config.serial == "SERIAL-2"
        assert provider.config.access_code == "code-2"
        assert provider.host == "192.0.2.20"
        assert not hasattr(provider, "printer")

    @pytest.mark.parametrize(
        "field",
        ["bambu_host", "bambu_serial", "bambu_access_code"],
    )
    def test_build_uses_core_credential_validation(self, field: str) -> None:
        values = {
            "bambu_host": "192.0.2.20",
            "bambu_serial": "SERIAL-2",
            "bambu_access_code": "code-2",
        }
        values[field] = ""
        printer = printer_config(
            "Bambu",
            provider=PrinterProvider.BAMBU_LAN,
            **values,
        )

        with pytest.raises(ProviderError) as error:
            BambuLanProvider.build(printer)

        assert error.value.detail == "provider_credentials_missing"
        assert error.value.code == "provider_credentials_missing"

    def test_constructor_maps_legacy_arguments_to_immutable_core_config(self) -> None:
        mqtt_factory = MagicMock()

        provider = BambuLanProvider(
            "192.0.2.10",
            "TEST-SERIAL",
            "test-code",
            mqtt_client_factory=mqtt_factory,
        )

        assert isinstance(provider, BambuClient)
        assert provider.config.host == "192.0.2.10"
        assert provider.config.serial == "TEST-SERIAL"
        assert provider.config.access_code == "test-code"
        assert provider._mqtt_client_factory is mqtt_factory
        assert provider.provider is PrinterProvider.BAMBU_LAN
        assert provider.capabilities is BambuClient.capabilities

    def test_class_level_ftps_factory_remains_compatible(self) -> None:
        client = BambuLanProvider._ftps_client()

        assert client.__class__.__name__ == "_ImplicitFTP_TLS"
