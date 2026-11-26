"""Whether a printer's configured address resolves, and why not when it does not.

The diagnostics endpoint runs this before talking to the printer. Its value is
the reason it reports: a ``.local`` name that cannot resolve inside a bridge
container is a different fix (use the IP) from a misspelled hostname, and both
differ from a printer that is merely off. The system resolver is stood in for
in every test; no lookup leaves the process.
"""

from __future__ import annotations

import socket
from typing import Any

import pytest
from printstash_core.printers import ProviderError

from app.db.models import PrinterProvider
from app.modules.printing import printer_address
from app.modules.printing.printer_address import (
    AddressFailure,
    check_printer_address,
    printer_hostname,
)
from tests.factories import printer_config


@pytest.fixture
def lookups(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every name sent to the resolver; resolve all of them."""

    seen: list[str] = []

    async def resolve(host: str) -> object:
        seen.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.50", 0))]

    monkeypatch.setattr(printer_address, "_system_resolve", resolve)
    return seen


@pytest.fixture
def unresolvable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def resolve(host: str) -> object:
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(printer_address, "_system_resolve", resolve)


class TestPrinterHostname:
    @pytest.mark.parametrize(
        ("provider", "field", "value", "expected"),
        [
            (
                PrinterProvider.MOONRAKER,
                "moonraker_url",
                "http://voron.local:7125",
                "voron.local",
            ),
            (
                PrinterProvider.PRUSALINK,
                "prusalink_url",
                "http://mk4.local/",
                "mk4.local",
            ),
            (
                PrinterProvider.OCTOPRINT,
                "octoprint_url",
                "https://octopi.lan/print/",
                "octopi.lan",
            ),
            (PrinterProvider.BAMBU_LAN, "bambu_host", "192.168.1.50", "192.168.1.50"),
            (
                PrinterProvider.ELEGOO_CENTAURI,
                "elegoo_centauri_host",
                " cc1.local ",
                "cc1.local",
            ),
        ],
    )
    def test_reads_the_host_from_the_provider_address_field(
        self, provider: PrinterProvider, field: str, value: str, expected: str
    ) -> None:
        address: dict[str, Any] = {field: value}
        printer = printer_config(provider=provider, **address)

        assert printer_hostname(printer) == expected

    def test_keeps_a_bare_ipv6_host_whole(self) -> None:
        printer = printer_config(
            provider=PrinterProvider.BAMBU_LAN, bambu_host="fe80::1"
        )

        assert printer_hostname(printer) == "fe80::1"

    def test_unwraps_a_bracketed_ipv6_url(self) -> None:
        printer = printer_config(moonraker_url="http://[fd00::5]:7125")

        assert printer_hostname(printer) == "fd00::5"

    def test_rejects_a_printer_without_an_address(self) -> None:
        printer = printer_config(moonraker_url="")

        with pytest.raises(ValueError, match="no address"):
            printer_hostname(printer)


class TestCheckPrinterAddress:
    @pytest.mark.asyncio
    async def test_accepts_an_ip_address_without_a_lookup(
        self, lookups: list[str]
    ) -> None:
        printer = printer_config(moonraker_url="http://192.168.1.50:7125")

        await check_printer_address(printer)

        assert lookups == []

    @pytest.mark.asyncio
    async def test_accepts_a_name_the_resolver_knows(self, lookups: list[str]) -> None:
        printer = printer_config(moonraker_url="http://voron.lan:7125")

        await check_printer_address(printer)

        assert lookups == ["voron.lan"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("host", ["mk4.local", "MK4.LOCAL", "mk4.local."])
    async def test_names_an_unresolved_local_name_as_mdns(
        self, unresolvable: None, host: str
    ) -> None:
        printer = printer_config(moonraker_url=f"http://{host}:7125")

        with pytest.raises(ProviderError) as raised:
            await check_printer_address(printer)

        assert raised.value.code == AddressFailure.MDNS_HOST_UNRESOLVED.value
        assert raised.value.detail == host.lower()

    @pytest.mark.asyncio
    async def test_names_any_other_unresolved_name_as_unresolved(
        self, unresolvable: None
    ) -> None:
        printer = printer_config(moonraker_url="http://voron.lan:7125")

        with pytest.raises(ProviderError) as raised:
            await check_printer_address(printer)

        assert raised.value.code == AddressFailure.HOST_UNRESOLVED.value
        assert raised.value.detail == "voron.lan"
