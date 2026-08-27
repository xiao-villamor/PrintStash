"""Tests for PrinterHub background worker."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import select

from app.db.models import (
    MaterialSlotState,
    MaterialSource,
    Printer,
    PrinterMaterialSlot,
    PrinterProvider,
    PrinterStatus,
    PrinterTool,
    PrintJob,
    PrintJobState,
)
from app.db.session import get_session_factory
from app.services import printer_hub as printer_hub_module
from app.services.printer_hub import PrinterHub
from app.services.realtime import InProcessBus
from app.services.spoolman import SpoolmanError
from tests.integration.api.v1.fleet._fleet_shared import _gcode

__all__ = [
    "AsyncMock",
    "InProcessBus",
    "MagicMock",
    "MaterialSlotState",
    "MaterialSource",
    "PrintJob",
    "PrintJobState",
    "Printer",
    "PrinterHub",
    "PrinterMaterialSlot",
    "PrinterProvider",
    "PrinterStatus",
    "PrinterTool",
    "SimpleNamespace",
    "SpoolmanError",
    "ThreadPoolExecutor",
    "_gcode",
    "asyncio",
    "get_session_factory",
    "patch",
    "printer_hub_module",
    "pytest",
    "select",
]
