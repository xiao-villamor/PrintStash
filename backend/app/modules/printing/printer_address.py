"""Whether PrintStash can turn a printer's configured address into an IP.

A printer that cannot be found by name fails every later check with a generic
transport error, which reads like a firewall or a powered-off printer. The
common cause in a container is narrower: names ending in ``.local`` are
resolved with multicast DNS, which does not cross Docker's bridge network, so
they never resolve there. Diagnostics run this check first so the operator sees
that reason, and the fix (use the printer's IP address), instead.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from enum import StrEnum
from urllib.parse import urlsplit

from printstash_core.printers import ProviderError

from app.db.models import Printer, PrinterProvider


class AddressFailure(StrEnum):
    """Why a configured printer address did not resolve."""

    HOST_UNRESOLVED = "printer_host_unresolved"
    MDNS_HOST_UNRESOLVED = "printer_mdns_host_unresolved"


_ADDRESS_FIELD: dict[PrinterProvider, str] = {
    PrinterProvider.MOONRAKER: "moonraker_url",
    PrinterProvider.BAMBU_LAN: "bambu_host",
    PrinterProvider.PRUSALINK: "prusalink_url",
    PrinterProvider.ELEGOO_CENTAURI: "elegoo_centauri_host",
    PrinterProvider.OCTOPRINT: "octoprint_url",
}


def printer_hostname(printer: Printer) -> str:
    """Return the host part of the provider's configured address.

    URL fields (``http://mk4.local:80``) and bare host fields
    (``192.168.1.50``, ``fe80::1``) both yield just the host.
    """

    value = str(getattr(printer, _ADDRESS_FIELD[printer.provider]) or "").strip()
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        pass
    hostname = urlsplit(value if "://" in value else f"//{value}").hostname
    if not hostname:
        raise ValueError(f"printer {printer.id} has no address to resolve")
    return hostname


async def _system_resolve(host: str) -> object:
    return await asyncio.get_running_loop().getaddrinfo(
        host, None, type=socket.SOCK_STREAM
    )


async def check_printer_address(printer: Printer) -> None:
    """Raise a ``ProviderError`` naming the reason when the address does not resolve."""

    host = printer_hostname(printer)
    try:
        ipaddress.ip_address(host)
        return
    except ValueError:
        pass
    try:
        await _system_resolve(host)
    except (socket.gaierror, UnicodeError) as exc:
        failure = (
            AddressFailure.MDNS_HOST_UNRESOLVED
            if host.lower().rstrip(".").endswith(".local")
            else AddressFailure.HOST_UNRESOLVED
        )
        raise ProviderError(host, code=failure.value) from exc
