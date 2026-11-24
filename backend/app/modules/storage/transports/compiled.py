"""Fail the full image build when a registered OpenDAL service is not compiled.

Run as ``python -m app.modules.storage.transports.compiled``. Constructing an
operator from its probe options performs no endpoint IO.
"""

from __future__ import annotations

import sys

from app.modules.storage.transports import TRANSPORTS
from app.modules.storage.transports.base import OpenDALService


def registered_services() -> tuple[OpenDALService, ...]:
    services: dict[str, OpenDALService] = {}
    for registered in TRANSPORTS:
        for runtime in (registered.vault_runtime, registered.remote_runtime):
            if isinstance(runtime, OpenDALService):
                services.setdefault(runtime.scheme, runtime)
    return tuple(services.values())


def missing_services() -> list[str]:
    import opendal

    missing = []
    for service in registered_services():
        try:
            opendal.Operator(service.scheme, **service.probe_options)
        except opendal.exceptions.Unsupported:
            missing.append(service.scheme)
    return missing


def main() -> int:
    missing = missing_services()
    if missing:
        print(
            "OpenDAL services not compiled: " + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
