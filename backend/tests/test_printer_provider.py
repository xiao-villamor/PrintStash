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
        # RUNNING is translated to Moonraker's "printing" vocabulary.
        assert out["print_stats"]["state"] == "printing"
        assert out["print_stats"]["filename"] == "cube.gcode"
        assert out["virtual_sdcard"]["progress"] == pytest.approx(0.45)

    @pytest.mark.parametrize(
        "bambu_state, moonraker_state",
        [
            ("RUNNING", "printing"),
            ("PAUSE", "paused"),  # regression: was passed through as "pause"
            ("FINISH", "complete"),  # regression: was "finish" -> UNKNOWN downstream
            ("FAILED", "error"),
            ("IDLE", "standby"),
            ("PREPARE", "standby"),
            ("SLICING", "standby"),
        ],
    )
    def test_normalize_status_translates_bambu_vocabulary(
        self, bambu_state, moonraker_state
    ):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        out = provider._normalize_status({"print": {"gcode_state": bambu_state}})
        assert out["print_stats"]["state"] == moonraker_state

    def test_normalize_status_progress_is_clamped(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        # Out-of-range mc_percent must not escape the 0..1 progress band.
        assert (
            provider._normalize_status({"print": {"mc_percent": 150}})["virtual_sdcard"][
                "progress"
            ]
            == 1.0
        )
        assert (
            provider._normalize_status({"print": {"mc_percent": None}})[
                "virtual_sdcard"
            ]["progress"]
            == 0.0
        )

    def test_normalized_bambu_states_are_known_to_status_map(self):
        # Every translated state must resolve to a concrete (non-UNKNOWN)
        # PrinterStatus, proving the provider and hub vocabularies agree.
        from app.db.models import PrinterStatus
        from app.services.printer_hub import _derive_printer_status

        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        for bambu_state in ("RUNNING", "PAUSE", "FINISH", "FAILED", "IDLE"):
            snap = provider._normalize_status({"print": {"gcode_state": bambu_state}})
            _, vault_status = _derive_printer_status(snap)
            assert vault_status != PrinterStatus.UNKNOWN, bambu_state

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
