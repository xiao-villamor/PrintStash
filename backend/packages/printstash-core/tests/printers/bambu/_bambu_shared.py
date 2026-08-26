"""Shared imports, fixtures, and builders for the tests printers bambu test modules."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import ssl
from ftplib import error_perm, error_reply
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import printstash_core.printers.bambu as bambu_module
from printstash_core.printers.bambu import (
    _BAMBU_CA_CERTIFICATES,
    BambuClient,
    BambuFactory,
    _ImplicitFTP_TLS,
)
from printstash_core.printers.contracts import (
    ArtifactCaptureClient,
    PrinterClient,
)
from printstash_core.printers.models import (
    BambuConfig,
    OctoPrintConfig,
    ProviderError,
    ProviderId,
)
from printstash_core.printers.registry import ProviderRegistry


def make_client(**kwargs: Any) -> BambuClient:
    return BambuClient(BambuConfig("192.0.2.10", "TEST-SERIAL", "test-code"), **kwargs)


class FakeMqttClient:
    def __init__(self) -> None:
        self.credentials: tuple[str, str] | None = None
        self.context: Any = None
        self.insecure: bool | None = None

    def username_pw_set(self, username: str, password: str) -> None:
        self.credentials = (username, password)

    def tls_set_context(self, context: Any) -> None:
        self.context = context

    def tls_insecure_set(self, insecure: bool) -> None:
        self.insecure = insecure


class FakeFtpsClient:
    def __init__(
        self, *, remote_size: int | None = None, download: bytes = b""
    ) -> None:
        self.remote_size = remote_size
        self.download = download
        self.calls: list[tuple[Any, ...]] = []
        self.uploaded = b""

    def connect(self, host: str, port: int) -> None:
        self.calls.append(("connect", host, port))

    def login(self, username: str, password: str) -> None:
        self.calls.append(("login", username, password))

    def prot_p(self) -> None:
        self.calls.append(("prot_p",))

    def storbinary(self, command: str, source: Any) -> None:
        self.calls.append(("storbinary", command))
        self.uploaded = source.read()

    def size(self, remote_name: str) -> int | None:
        self.calls.append(("size", remote_name))
        if self.remote_size is not None:
            return self.remote_size
        return len(self.uploaded or self.download)

    def rename(self, source: str, destination: str) -> None:
        self.calls.append(("rename", source, destination))

    def retrbinary(self, command: str, callback: Any) -> None:
        self.calls.append(("retrbinary", command))
        callback(self.download)

    def quit(self) -> None:
        self.calls.append(("quit",))

    def close(self) -> None:
        self.calls.append(("close",))


class FailingFtpsClient(FakeFtpsClient):
    def __init__(self, failure: BaseException | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.failure = failure

    def connect(self, host: str, port: int) -> None:
        super().connect(host, port)
        if self.failure is not None:
            failure, self.failure = self.failure, None
            raise failure


__all__ = [
    "Any",
    "ArtifactCaptureClient",
    "BambuClient",
    "BambuConfig",
    "BambuFactory",
    "FailingFtpsClient",
    "FakeFtpsClient",
    "FakeMqttClient",
    "OctoPrintConfig",
    "Path",
    "PrinterClient",
    "ProviderError",
    "ProviderId",
    "ProviderRegistry",
    "SimpleNamespace",
    "_BAMBU_CA_CERTIFICATES",
    "_ImplicitFTP_TLS",
    "asyncio",
    "bambu_module",
    "error_perm",
    "error_reply",
    "hashlib",
    "json",
    "logging",
    "make_client",
    "pytest",
    "ssl",
]
