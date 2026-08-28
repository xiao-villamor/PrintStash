"""Building a Bambu client from stored configuration, through the registry.

The registry is the one place the application turns a `Printer` row into a
transport, so this is where injected seams either survive or are silently
dropped. Every seam this factory carries exists for testing or for operational
control — the MQTT client, the FTPS client, the sequence-id source, the logger —
and a factory that forgot one would leave production reaching for a real socket
from inside a test, or a test passing against a stub the real path never uses.

The provider-mismatch refusal matters for a different reason: `ProviderId` and
the stored config are two independent columns, so a row can name Bambu while
holding an OctoPrint config. That has to fail as configuration, loudly, not as an
`AttributeError` on the first status poll.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from printstash_core.printers.bambu import BambuClient, BambuFactory
from printstash_core.printers.models import (
    BambuConfig,
    OctoPrintConfig,
    ProviderError,
    ProviderId,
)
from printstash_core.printers.registry import ProviderRegistry

from .conftest import ACCESS_CODE, HOST, SERIAL


def seams() -> dict[str, Any]:
    return {
        "mqtt_client_factory": lambda: object(),
        "ftps_client_factory": lambda: object(),
        "sequence_id_factory": lambda: "fixed-sequence",
        "logger": logging.getLogger("test.bambu.factory"),
    }


class TestBambuFactory:
    def test_builds_a_bambu_client_through_the_registry(self) -> None:
        registry = ProviderRegistry([BambuFactory()])

        client = registry.build(
            ProviderId.BAMBU_LAN, BambuConfig(HOST, SERIAL, ACCESS_CODE)
        )

        assert isinstance(client, BambuClient)

    def test_hands_the_stored_configuration_to_the_client_untouched(self) -> None:
        config = BambuConfig(HOST, SERIAL, ACCESS_CODE)

        client = BambuFactory().build(config)

        assert client.config is config

    def test_passes_every_injected_seam_to_the_client(self) -> None:
        injected = seams()

        client = BambuFactory(**injected).build(BambuConfig(HOST, SERIAL, ACCESS_CODE))

        # A dropped seam means a test that thinks it stubbed the network and a
        # production path that reaches for a real socket.
        assert client._mqtt_client_factory is injected["mqtt_client_factory"]
        assert client._ftps_client_factory is injected["ftps_client_factory"]
        assert client._sequence_id_factory is injected["sequence_id_factory"]
        assert client._logger is injected["logger"]

    def test_advertises_the_clients_capabilities_before_building_one(self) -> None:
        factory = BambuFactory()

        # The UI asks the factory what a provider can do while the operator is
        # still filling in the form, so the answer cannot require a client.
        assert factory.capabilities is BambuClient.capabilities

    def test_refuses_a_configuration_for_another_provider(self) -> None:
        with pytest.raises(ProviderError) as error:
            BambuFactory().build(OctoPrintConfig("http://octoprint.local", "key"))

        # Provider id and config are separate columns; a mismatch is a
        # configuration error, not an AttributeError on the first poll.
        assert error.value.code == "provider_config_mismatch"
        assert error.value.detail == "provider_config_mismatch"
