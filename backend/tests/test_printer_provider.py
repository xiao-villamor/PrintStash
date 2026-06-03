from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.db.models import Printer, PrinterProvider
from app.services.printer_provider import (
    BambuLanProvider,
    MoonrakerProvider,
    ProviderError,
    capabilities_for_provider,
    get_provider_client,
)


class TestCapabilities:
    def test_moonraker_capabilities(self):
        caps = capabilities_for_provider(PrinterProvider.MOONRAKER)
        assert caps.can_upload is True
        assert caps.can_pause is True
        assert caps.support_level == "stable"

    def test_bambu_capabilities(self):
        caps = capabilities_for_provider(PrinterProvider.BAMBU_LAN)
        assert caps.can_upload is False
        assert caps.can_pause is True
        assert caps.support_level == "beta"
        assert "upload" in caps.unsupported_actions


class TestProviderFactory:
    def test_get_moonraker_provider(self):
        p = Printer(
            name="mk",
            provider=PrinterProvider.MOONRAKER,
            moonraker_url="http://10.0.0.1:7125",
        )
        client = get_provider_client(p)
        assert isinstance(client, MoonrakerProvider)

    def test_get_bambu_provider(self):
        p = Printer(
            name="bambu",
            provider=PrinterProvider.BAMBU_LAN,
            moonraker_url="",
            bambu_host="192.168.1.50",
            bambu_serial="SN123",
            bambu_access_code="acc",
        )
        client = get_provider_client(p)
        assert isinstance(client, BambuLanProvider)

    def test_missing_bambu_creds_raises(self):
        p = Printer(
            name="bambu",
            provider=PrinterProvider.BAMBU_LAN,
            moonraker_url="",
        )
        with pytest.raises(ProviderError, match="provider_credentials_missing"):
            get_provider_client(p)


class TestBambuLanProvider:
    def test_normalize_status_maps_expected_shape(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        out = provider._normalize_status(
            {
                "print": {
                    "gcode_state": "RUNNING",
                    "mc_percent": 45,
                    "subtask_name": "cube.gcode",
                }
            }
        )
        assert out["print_stats"]["state"] == "running"
        assert out["print_stats"]["filename"] == "cube.gcode"
        assert out["virtual_sdcard"]["progress"] == pytest.approx(0.45)

    def test_pause_resume_cancel_commands(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        with patch.object(provider, "_send_command", new_callable=AsyncMock) as send:
            send.return_value = {"ok": True}
            asyncio.run(provider.pause())
            asyncio.run(provider.resume())
            asyncio.run(provider.cancel())
            assert send.await_count == 3

    def test_start_unsupported(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        with pytest.raises(ProviderError, match="operation_not_supported_for_provider"):
            asyncio.run(provider.start("file.gcode"))
