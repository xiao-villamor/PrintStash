"""What each provider needs before PrintStash will build a client for it.

Every provider is configured through the same `Printer` row, and the row has a column for
each provider's credentials. So a half-configured printer — a Bambu with no access code, a
PrusaLink in digest mode with no username — is a row that looks valid and produces a client
that fails at the moment somebody tries to print, in the middle of a queue, with no useful
message.

`build` is where that is caught instead. Each provider names exactly the fields it cannot
work without, and a missing one is `provider_credentials_missing` **before** any network
call, so the failure lands in the settings form where it can be fixed rather than on the
shop floor.

PrusaLink and Elegoo are the interesting ones: what they require depends on another field.
PrusaLink needs a username and password in digest mode and an API key in api-key mode;
Elegoo's second-generation variant needs an access code its predecessor does not. Getting
that conditional wrong would let one configuration through while blocking the other.
"""

from __future__ import annotations

import pytest

from app.db.models import PrinterProvider
from app.services.printer_provider import (
    ElegooCentauriProvider,
    MoonrakerProvider,
    OctoPrintProvider,
    ProviderError,
    PrusaLinkProvider,
)
from tests.factories import printer_config


class TestMoonrakerBuild:
    def test_builds_from_a_url(self) -> None:
        printer = printer_config(
            "Ender", credentials=False, moonraker_url="http://10.0.0.1:7125"
        )

        assert MoonrakerProvider.build(printer) is not None

    def test_refuses_a_printer_with_no_url(self) -> None:
        printer = printer_config("Ender", credentials=False, moonraker_url="")

        with pytest.raises(ProviderError, match="provider_credentials_missing"):
            MoonrakerProvider.build(printer)


class TestPrusaLinkBuild:
    def test_builds_digest_credentials_from_a_user_pair(self) -> None:
        printer = printer_config(
            "MK4",
            credentials=False,
            provider=PrinterProvider.PRUSALINK,
            moonraker_url="",
            prusalink_url="http://10.0.0.2",
            prusalink_auth_mode="digest",
            prusalink_username="maker",
            prusalink_password="secret",
        )

        assert PrusaLinkProvider.build(printer) is not None

    def test_builds_in_api_key_mode_with_a_key(self) -> None:
        printer = printer_config(
            "MK4",
            credentials=False,
            provider=PrinterProvider.PRUSALINK,
            moonraker_url="",
            prusalink_url="http://10.0.0.2",
            prusalink_auth_mode="api_key",
            prusalink_api_key="key",
        )

        assert PrusaLinkProvider.build(printer) is not None

    def test_refuses_a_printer_with_no_url(self) -> None:
        printer = printer_config(
            "MK4",
            credentials=False,
            provider=PrinterProvider.PRUSALINK,
            moonraker_url="",
            prusalink_auth_mode="digest",
        )

        with pytest.raises(ProviderError, match="provider_credentials_missing"):
            PrusaLinkProvider.build(printer)

    def test_refuses_digest_mode_with_no_username(self) -> None:
        printer = printer_config(
            "MK4",
            credentials=False,
            provider=PrinterProvider.PRUSALINK,
            moonraker_url="",
            prusalink_url="http://10.0.0.2",
            prusalink_auth_mode="digest",
            prusalink_password="secret",
        )

        # The conditional is the whole point: an api-key printer legitimately
        # has no username, so the check has to depend on the mode.
        with pytest.raises(ProviderError, match="provider_credentials_missing"):
            PrusaLinkProvider.build(printer)

    def test_refuses_api_key_mode_with_no_key(self) -> None:
        printer = printer_config(
            "MK4",
            credentials=False,
            provider=PrinterProvider.PRUSALINK,
            moonraker_url="",
            prusalink_url="http://10.0.0.2",
            prusalink_auth_mode="api_key",
        )

        with pytest.raises(ProviderError, match="provider_credentials_missing"):
            PrusaLinkProvider.build(printer)


class TestOctoPrintBuild:
    def test_builds_api_key_credentials_from_a_url_pair(self) -> None:
        printer = printer_config(
            "Octo",
            credentials=False,
            provider=PrinterProvider.OCTOPRINT,
            moonraker_url="",
            octoprint_url="http://10.0.0.3",
            octoprint_api_key="key",
        )

        assert OctoPrintProvider.build(printer) is not None

    @pytest.mark.parametrize(
        "missing", ["octoprint_url", "octoprint_api_key"], ids=["no-url", "no-key"]
    )
    def test_refuses_a_printer_missing_either_half(self, missing: str) -> None:
        fields = {
            "octoprint_url": "http://10.0.0.3",
            "octoprint_api_key": "key",
        }
        fields[missing] = ""
        printer = printer_config(
            "Octo",
            credentials=False,
            provider=PrinterProvider.OCTOPRINT,
            moonraker_url="",
            **fields,
        )

        with pytest.raises(ProviderError, match="provider_credentials_missing"):
            OctoPrintProvider.build(printer)


class TestElegooCentauriBuild:
    def test_builds_the_first_generation_from_a_host(self) -> None:
        printer = printer_config(
            "Centauri",
            credentials=False,
            provider=PrinterProvider.ELEGOO_CENTAURI,
            moonraker_url="",
            provider_variant="elegoo_centauri_carbon",
            elegoo_centauri_host="10.0.0.4",
        )

        assert ElegooCentauriProvider.build(printer) is not None

    def test_builds_the_second_generation_with_an_access_code(self) -> None:
        printer = printer_config(
            "Centauri 2",
            credentials=False,
            provider=PrinterProvider.ELEGOO_CENTAURI,
            moonraker_url="",
            provider_variant="elegoo_centauri_carbon_2",
            elegoo_centauri_host="10.0.0.4",
            elegoo_centauri_access_code="code",
        )

        assert ElegooCentauriProvider.build(printer) is not None

    def test_refuses_a_printer_with_no_host(self) -> None:
        printer = printer_config(
            "Centauri",
            credentials=False,
            provider=PrinterProvider.ELEGOO_CENTAURI,
            moonraker_url="",
            provider_variant="elegoo_centauri_carbon",
        )

        with pytest.raises(ProviderError, match="provider_credentials_missing"):
            ElegooCentauriProvider.build(printer)

    def test_refuses_a_variant_it_does_not_support(self) -> None:
        printer = printer_config(
            "Centauri X",
            credentials=False,
            provider=PrinterProvider.ELEGOO_CENTAURI,
            moonraker_url="",
            provider_variant="elegoo_centauri_future",
            elegoo_centauri_host="10.0.0.4",
        )

        # A variant nobody has written a client for must fail at configuration
        # rather than produce a client that speaks the wrong protocol.
        with pytest.raises(ProviderError, match="provider_credentials_missing"):
            ElegooCentauriProvider.build(printer)

    def test_refuses_the_second_generation_with_no_access_code(self) -> None:
        printer = printer_config(
            "Centauri 2",
            credentials=False,
            provider=PrinterProvider.ELEGOO_CENTAURI,
            moonraker_url="",
            provider_variant="elegoo_centauri_carbon_2",
            elegoo_centauri_host="10.0.0.4",
        )

        # Its predecessor needs no code, so this too has to depend on the
        # variant rather than on the provider alone.
        with pytest.raises(ProviderError, match="provider_credentials_missing"):
            ElegooCentauriProvider.build(printer)
