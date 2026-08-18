from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from printstash_core.printers import (
    BambuConfig,
    ElegooCentauriConfig,
    MoonrakerConfig,
    OctoPrintConfig,
    ProviderError,
    ProviderId,
    PrusaLinkConfig,
)


@pytest.mark.parametrize(
    "config, provider_id",
    [
        (MoonrakerConfig("http://printer.local"), ProviderId.MOONRAKER),
        (BambuConfig("printer.local", "SERIAL", "code"), ProviderId.BAMBU_LAN),
        (
            PrusaLinkConfig(
                "http://prusa.local", "digest", username="user", password="pass"
            ),
            ProviderId.PRUSALINK,
        ),
        (
            PrusaLinkConfig("http://prusa.local", "api_key", api_key="key"),
            ProviderId.PRUSALINK,
        ),
        (
            OctoPrintConfig("http://octoprint.local", "key"),
            ProviderId.OCTOPRINT,
        ),
        (
            ElegooCentauriConfig("centauri.local", "elegoo_centauri_carbon"),
            ProviderId.ELEGOO_CENTAURI,
        ),
        (
            ElegooCentauriConfig("centauri.local", "elegoo_centauri_carbon_2", "code"),
            ProviderId.ELEGOO_CENTAURI,
        ),
    ],
)
def test_valid_configs_are_frozen(config: object, provider_id: ProviderId) -> None:
    assert config.provider_id is provider_id
    with pytest.raises(FrozenInstanceError):
        config.unexpected = True


@pytest.mark.parametrize(
    "factory",
    [
        lambda: MoonrakerConfig(" "),
        lambda: BambuConfig("printer.local", "", "code"),
        lambda: PrusaLinkConfig("http://prusa.local", "digest", username="user"),
        lambda: PrusaLinkConfig("http://prusa.local", "api_key"),
        lambda: PrusaLinkConfig("http://prusa.local", "unknown"),
        lambda: OctoPrintConfig("http://octoprint.local", ""),
        lambda: ElegooCentauriConfig("centauri.local", "unknown"),
        lambda: ElegooCentauriConfig("centauri.local", "elegoo_centauri_carbon_2"),
    ],
)
def test_missing_or_invalid_credentials_have_one_error_surface(factory: object) -> None:
    with pytest.raises(ProviderError) as error:
        factory()  # type: ignore[operator]

    assert error.value.detail == "provider_credentials_missing"
    assert error.value.code == "provider_credentials_missing"
