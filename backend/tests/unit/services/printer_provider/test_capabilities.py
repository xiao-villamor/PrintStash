"""Defends capabilities at the services printer provider unit boundary.

A regression would misclassify provider capability, status, or transport behavior.
"""

from __future__ import annotations

from ._printer_provider_shared import (
    BambuConfig,
    BambuLanProvider,
    ElegooCentauriConfig,
    ElegooCentauriProvider,
    MoonrakerConfig,
    MoonrakerProvider,
    OctoPrintConfig,
    OctoPrintProvider,
    Printer,
    PrinterProvider,
    ProviderError,
    PrusaLinkConfig,
    PrusaLinkProvider,
    build_provider_registry,
    capabilities_for_provider,
    detect_printer_model,
    get_provider_client,
    inspect,
    printer_config_from_model,
    pytest,
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
        assert caps.can_upload is True
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
    def test_product_registry_is_instance_owned(self):
        first = build_provider_registry()
        second = build_provider_registry()

        assert first is not second
        assert {provider.value for provider in first.providers} == {
            provider.value for provider in PrinterProvider
        }

    def test_provider_client_requires_a_composed_registry(self):
        parameter = inspect.signature(get_provider_client).parameters["registry"]

        assert parameter.default is inspect.Parameter.empty

    def test_orm_record_is_copied_to_neutral_config(self):
        p = Printer(
            name="mk",
            provider=PrinterProvider.MOONRAKER,
            moonraker_url="http://10.0.0.1:7125",
            api_key="secret",
        )

        config = printer_config_from_model(p)

        assert config.base_url == "http://10.0.0.1:7125"
        assert config.api_key == "secret"

    def test_get_provider_uses_injected_registry(self):
        p = Printer(
            name="mk",
            provider=PrinterProvider.MOONRAKER,
            moonraker_url="http://10.0.0.1:7125",
        )

        client = get_provider_client(p, registry=build_provider_registry())

        assert isinstance(client, MoonrakerProvider)

    @pytest.fixture(autouse=True)
    def _registry(self):
        self.registry = build_provider_registry()

    def test_get_moonraker_provider(self):
        p = Printer(
            name="mk",
            provider=PrinterProvider.MOONRAKER,
            moonraker_url="http://10.0.0.1:7125",
        )
        client = get_provider_client(p, registry=self.registry)
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
        client = get_provider_client(p, registry=self.registry)
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
        client = get_provider_client(p, registry=self.registry)
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
            get_provider_client(p, registry=self.registry)
        assert exc.value.code == "provider_credentials_missing"

    def test_get_centauri_carbon_provider(self):
        p = Printer(
            name="CC1",
            provider=PrinterProvider.ELEGOO_CENTAURI,
            provider_variant="elegoo_centauri_carbon",
            elegoo_centauri_host="192.168.1.50",
        )
        assert isinstance(
            get_provider_client(p, registry=self.registry), ElegooCentauriProvider
        )

    def test_centauri_carbon_2_requires_access_code(self):
        p = Printer(
            name="CC2",
            provider=PrinterProvider.ELEGOO_CENTAURI,
            provider_variant="elegoo_centauri_carbon_2",
            elegoo_centauri_host="192.168.1.51",
        )
        with pytest.raises(ProviderError) as exc:
            get_provider_client(p, registry=self.registry)
        assert exc.value.code == "provider_credentials_missing"

    def test_missing_bambu_creds_raises(self):
        p = Printer(
            name="bambu",
            provider=PrinterProvider.BAMBU_LAN,
            moonraker_url="",
        )
        with pytest.raises(ProviderError, match="provider_credentials_missing"):
            get_provider_client(p, registry=self.registry)

    def test_get_octoprint_provider(self):
        p = Printer(
            name="octopi",
            provider=PrinterProvider.OCTOPRINT,
            octoprint_url="http://octopi.local",
            octoprint_api_key="key-123",
        )
        client = get_provider_client(p, registry=self.registry)
        assert isinstance(client, OctoPrintProvider)

    def test_octoprint_missing_credentials_rejected(self):
        p = Printer(
            name="octopi",
            provider=PrinterProvider.OCTOPRINT,
            octoprint_url="http://octopi.local",
        )
        with pytest.raises(ProviderError) as exc:
            get_provider_client(p, registry=self.registry)
        assert exc.value.code == "provider_credentials_missing"

    def test_moonraker_missing_url_is_rejected(self):
        printer = Printer(name="moonraker", provider=PrinterProvider.MOONRAKER)

        with pytest.raises(ProviderError) as exc_info:
            MoonrakerProvider.build(printer)

        assert exc_info.value.code == "provider_credentials_missing"

    def test_prusalink_api_key_credentials_build_provider(self):
        printer = Printer(
            name="mk4",
            provider=PrinterProvider.PRUSALINK,
            prusalink_url="http://mk4.local",
            prusalink_auth_mode="api_key",
            prusalink_api_key="fake-api-key",
        )

        provider = PrusaLinkProvider.build(printer)

        assert isinstance(provider, PrusaLinkProvider)

    def test_prusalink_api_key_mode_requires_key(self):
        printer = Printer(
            name="mk4",
            provider=PrinterProvider.PRUSALINK,
            prusalink_url="http://mk4.local",
            prusalink_auth_mode="api_key",
        )

        with pytest.raises(ProviderError) as exc_info:
            PrusaLinkProvider.build(printer)

        assert exc_info.value.code == "provider_credentials_missing"

    def test_centauri_unknown_variant_is_rejected(self):
        printer = Printer(
            name="unknown-centauri",
            provider=PrinterProvider.ELEGOO_CENTAURI,
            provider_variant="unknown",
            elegoo_centauri_host="192.0.2.20",
        )

        with pytest.raises(ProviderError) as exc_info:
            ElegooCentauriProvider.build(printer)

        assert exc_info.value.code == "provider_credentials_missing"

    def test_centauri_carbon_2_with_access_code_builds_provider(self):
        printer = Printer(
            name="cc2",
            provider=PrinterProvider.ELEGOO_CENTAURI,
            provider_variant="elegoo_centauri_carbon_2",
            elegoo_centauri_host="192.0.2.21",
            elegoo_centauri_access_code="12345678",
        )

        provider = ElegooCentauriProvider.build(printer)

        assert isinstance(provider, ElegooCentauriProvider)

    @pytest.mark.parametrize(
        ("printer", "expected"),
        [
            pytest.param(
                Printer(
                    name="moonraker",
                    provider=PrinterProvider.MOONRAKER,
                    moonraker_url="http://192.0.2.1:7125",
                    api_key="fake-key",
                    provider_variant="elegoo_neptune4",
                ),
                MoonrakerConfig(
                    base_url="http://192.0.2.1:7125",
                    api_key="fake-key",
                    variant="elegoo_neptune4",
                ),
                id="moonraker",
            ),
            pytest.param(
                Printer(
                    name="bambu",
                    provider=PrinterProvider.BAMBU_LAN,
                    bambu_host="192.0.2.2",
                    bambu_serial="FAKE-SERIAL",
                    bambu_access_code="12345678",
                ),
                BambuConfig(
                    host="192.0.2.2", serial="FAKE-SERIAL", access_code="12345678"
                ),
                id="bambu-lan",
            ),
            pytest.param(
                Printer(
                    name="prusalink",
                    provider=PrinterProvider.PRUSALINK,
                    prusalink_url="http://192.0.2.3",
                    prusalink_auth_mode="api_key",
                    prusalink_api_key="fake-key",
                ),
                PrusaLinkConfig(
                    base_url="http://192.0.2.3",
                    auth_mode="api_key",
                    username=None,
                    password=None,
                    api_key="fake-key",
                ),
                id="prusalink",
            ),
            pytest.param(
                Printer(
                    name="octoprint",
                    provider=PrinterProvider.OCTOPRINT,
                    octoprint_url="http://192.0.2.4",
                    octoprint_api_key="fake-key",
                ),
                OctoPrintConfig(base_url="http://192.0.2.4", api_key="fake-key"),
                id="octoprint",
            ),
            pytest.param(
                Printer(
                    name="centauri",
                    provider=PrinterProvider.ELEGOO_CENTAURI,
                    provider_variant="elegoo_centauri_carbon",
                    elegoo_centauri_host="192.0.2.5",
                    elegoo_centauri_mainboard_id="FAKE-BOARD",
                ),
                ElegooCentauriConfig(
                    host="192.0.2.5",
                    model="elegoo_centauri_carbon",
                    access_code=None,
                    mainboard_id="FAKE-BOARD",
                ),
                id="elegoo-centauri",
            ),
        ],
    )
    def test_printer_record_copies_to_neutral_config(self, printer, expected):
        assert printer_config_from_model(printer) == expected

    def test_unknown_provider_record_is_rejected(self):
        printer = Printer(name="unknown", provider=PrinterProvider.MOONRAKER)
        object.__setattr__(printer, "provider", "unknown")

        with pytest.raises(ProviderError) as exc_info:
            printer_config_from_model(printer)

        assert exc_info.value.code == "unknown_provider"
