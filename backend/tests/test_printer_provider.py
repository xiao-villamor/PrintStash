from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db.models import Printer, PrinterProvider
from app.services.printer_provider import (
    BambuLanProvider,
    ElegooCentauriProvider,
    MoonrakerProvider,
    OctoPrintProvider,
    ProviderError,
    PrusaLinkProvider,
    capabilities_for_provider,
    detect_printer_model,
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
        assert caps.can_upload is True
        assert caps.can_start is True
        assert caps.can_pause is True
        assert caps.support_level == "beta"
        assert "list_files" in caps.unsupported_actions

    def test_prusalink_capabilities_are_beta_and_honest(self):
        caps = PrusaLinkProvider.capabilities
        assert caps.can_upload is True
        assert caps.can_start is True
        assert caps.can_list_files is True
        assert caps.can_send_gcode is False
        assert caps.can_measure_consumption is False
        assert caps.support_level == "beta"

    def test_centauri_capabilities_are_safe_and_honest(self):
        caps = ElegooCentauriProvider.capabilities
        assert caps.can_live_status is True
        assert caps.can_start is True
        assert caps.can_pause is True
        assert caps.can_upload is False
        assert caps.can_list_files is False
        assert caps.can_send_gcode is False
        assert caps.support_level == "beta"

    def test_octoprint_capabilities_are_beta_and_honest(self):
        caps = OctoPrintProvider.capabilities
        assert caps.can_upload is True
        assert caps.can_start is True
        assert caps.can_list_files is True
        assert caps.can_send_gcode is False
        assert caps.can_measure_consumption is False
        assert caps.support_level == "beta"


class TestDetectPrinterModel:
    def test_detects_bambu_model_from_serial_prefix(self):
        p = Printer(
            name="X1C",
            provider=PrinterProvider.BAMBU_LAN,
            bambu_serial="01P00A123456",
        )
        assert detect_printer_model(p) == "Bambu Lab X1 Carbon"

    def test_unknown_bambu_serial_prefix_returns_none(self):
        p = Printer(
            name="Mystery",
            provider=PrinterProvider.BAMBU_LAN,
            bambu_serial="ZZZ00A123456",
        )
        assert detect_printer_model(p) is None

    def test_detects_elegoo_neptune4_from_provider_variant(self):
        p = Printer(
            name="Neptune",
            provider=PrinterProvider.MOONRAKER,
            provider_variant="elegoo_neptune4",
        )
        assert detect_printer_model(p) == "Elegoo Neptune 4 family"

    def test_detects_elegoo_centauri_carbon_2_from_provider_variant(self):
        p = Printer(
            name="Centauri",
            provider=PrinterProvider.ELEGOO_CENTAURI,
            provider_variant="elegoo_centauri_carbon_2",
        )
        assert detect_printer_model(p) == "Elegoo Centauri Carbon 2"

    def test_plain_moonraker_is_undetectable(self):
        p = Printer(name="Voron", provider=PrinterProvider.MOONRAKER)
        assert detect_printer_model(p) is None


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

    def test_get_prusalink_digest_provider(self):
        p = Printer(
            name="mk4",
            provider=PrinterProvider.PRUSALINK,
            prusalink_url="http://mk4.local",
            prusalink_auth_mode="digest",
            prusalink_username="maker",
            prusalink_password="secret",
        )
        client = get_provider_client(p)
        assert isinstance(client, PrusaLinkProvider)

    def test_prusalink_missing_credentials_rejected(self):
        p = Printer(
            name="mk4",
            provider=PrinterProvider.PRUSALINK,
            prusalink_url="http://mk4.local",
            prusalink_auth_mode="digest",
            prusalink_username="maker",
        )
        with pytest.raises(ProviderError) as exc:
            get_provider_client(p)
        assert exc.value.code == "provider_credentials_missing"

    def test_get_centauri_carbon_provider(self):
        p = Printer(
            name="CC1",
            provider=PrinterProvider.ELEGOO_CENTAURI,
            provider_variant="elegoo_centauri_carbon",
            elegoo_centauri_host="192.168.1.50",
        )
        assert isinstance(get_provider_client(p), ElegooCentauriProvider)

    def test_centauri_carbon_2_requires_access_code(self):
        p = Printer(
            name="CC2",
            provider=PrinterProvider.ELEGOO_CENTAURI,
            provider_variant="elegoo_centauri_carbon_2",
            elegoo_centauri_host="192.168.1.51",
        )
        with pytest.raises(ProviderError) as exc:
            get_provider_client(p)
        assert exc.value.code == "provider_credentials_missing"

    def test_missing_bambu_creds_raises(self):
        p = Printer(
            name="bambu",
            provider=PrinterProvider.BAMBU_LAN,
            moonraker_url="",
        )
        with pytest.raises(ProviderError, match="provider_credentials_missing"):
            get_provider_client(p)

    def test_get_octoprint_provider(self):
        p = Printer(
            name="octopi",
            provider=PrinterProvider.OCTOPRINT,
            octoprint_url="http://octopi.local",
            octoprint_api_key="key-123",
        )
        client = get_provider_client(p)
        assert isinstance(client, OctoPrintProvider)

    def test_octoprint_missing_credentials_rejected(self):
        p = Printer(
            name="octopi",
            provider=PrinterProvider.OCTOPRINT,
            octoprint_url="http://octopi.local",
        )
        with pytest.raises(ProviderError) as exc:
            get_provider_client(p)
        assert exc.value.code == "provider_credentials_missing"


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
            provider._normalize_status({"print": {"mc_percent": 150}})[
                "virtual_sdcard"
            ]["progress"]
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

    def test_start_sends_cached_gcode_command(self):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        with patch.object(provider, "_send_command", new_callable=AsyncMock) as send:
            send.return_value = {"ok": True}
            assert asyncio.run(provider.start("file.gcode")) == {"ok": True}
        payload = send.await_args.args[0]
        assert payload["print"]["command"] == "gcode_file"
        assert payload["print"]["param"] == "/cache/file.gcode"
        assert payload["print"]["sequence_id"]

    def test_upload_uses_ftps_adapter(self, tmp_path: Path):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        source = tmp_path / "cube.gcode"
        source.write_text("G28\n")
        with patch.object(provider, "_upload_via_ftps") as upload:
            assert asyncio.run(provider.upload(source, "cube.gcode")) == {
                "ok": True,
                "remote_filename": "cube.gcode",
            }
        upload.assert_called_once_with(source, "cube.gcode")

    def test_upload_rejects_nested_remote_name(self, tmp_path: Path):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        source = tmp_path / "cube.gcode"
        source.write_text("G28\n")
        with pytest.raises(ProviderError, match="invalid_bambu_remote_filename"):
            provider._upload_via_ftps(source, "nested/cube.gcode")

    def test_ftps_upload_uses_cache_and_atomic_rename(self, tmp_path: Path):
        provider = BambuLanProvider("192.168.1.50", "SN123", "acc")
        source = tmp_path / "cube.gcode"
        source.write_bytes(b"G28\n")
        ftp = MagicMock()
        ftp.size.return_value = source.stat().st_size
        with patch.object(provider, "_ftps_client", return_value=ftp):
            provider._upload_via_ftps(source, "cube.gcode")

        ftp.connect.assert_called_once_with("192.168.1.50", 990)
        ftp.login.assert_called_once_with("bblp", "acc")
        ftp.prot_p.assert_called_once_with()
        upload_path = ftp.storbinary.call_args.args[0]
        assert upload_path.startswith("STOR cache/.cube.gcode.")
        assert upload_path.endswith(".uploading")
        temp_path = upload_path.removeprefix("STOR ")
        ftp.size.assert_called_once_with(temp_path)
        ftp.rename.assert_called_once_with(temp_path, "cache/cube.gcode")
        ftp.quit.assert_called_once_with()
