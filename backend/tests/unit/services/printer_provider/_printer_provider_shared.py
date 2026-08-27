"""Provider composition preserves honest capabilities and credential gates.

These tests defend the application-level adapter boundary independently from
wire-protocol tests so unsupported hardware operations cannot reach I/O.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from paho.mqtt import client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode
from printstash_core.printers import (
    BambuConfig,
    ElegooCentauriConfig,
    MoonrakerConfig,
    OctoPrintConfig,
    PrusaLinkConfig,
)

from app.db.models import Printer, PrinterProvider
from app.services.printer_provider import (
    BambuLanProvider,
    BaseProvider,
    Capability,
    ElegooCentauriProvider,
    MoonrakerProvider,
    OctoPrintProvider,
    ProviderCapabilities,
    ProviderError,
    PrusaLinkProvider,
    build_provider_registry,
    capabilities_for_provider,
    detect_printer_model,
    get_provider_client,
    printer_config_from_model,
)


def _fake_mqtt_client() -> MagicMock:
    """MagicMock shaped like the real paho client but *without* a ``socket``
    attribute, so ``_validate_mqtt_peer``'s "real paho has it" bypass applies
    (mirrors tests/fakes/mock_bambu.FakeMqttClient, which is only wired
    for the full print-flow integration tests, not raw MQTT error branches)."""
    return MagicMock(
        spec=[
            "username_pw_set",
            "tls_set_context",
            "tls_insecure_set",
            "connect",
            "subscribe",
            "loop_start",
            "loop_stop",
            "disconnect",
            "publish",
            "on_connect",
            "on_message",
        ]
    )


__all__ = [
    "AsyncMock",
    "BambuConfig",
    "BambuLanProvider",
    "BaseProvider",
    "Capability",
    "ElegooCentauriConfig",
    "ElegooCentauriProvider",
    "MagicMock",
    "MoonrakerConfig",
    "MoonrakerProvider",
    "OctoPrintConfig",
    "OctoPrintProvider",
    "PacketTypes",
    "Path",
    "Printer",
    "PrinterProvider",
    "ProviderCapabilities",
    "ProviderError",
    "PrusaLinkConfig",
    "PrusaLinkProvider",
    "ReasonCode",
    "_fake_mqtt_client",
    "asyncio",
    "build_provider_registry",
    "capabilities_for_provider",
    "detect_printer_model",
    "get_provider_client",
    "inspect",
    "json",
    "mqtt",
    "patch",
    "printer_config_from_model",
    "pytest",
]
