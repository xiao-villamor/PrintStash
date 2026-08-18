from __future__ import annotations

from printstash_core.printers.catalog import (
    PROVIDER_DEFINITIONS,
    SETUP_OPTIONS,
    catalog_document,
)
from printstash_core.printers.models import Capability, ProviderId


def test_catalog_covers_every_shared_provider_once() -> None:
    assert set(PROVIDER_DEFINITIONS) == set(ProviderId)
    assert len(PROVIDER_DEFINITIONS) == len(ProviderId)
    assert {option.provider_id for option in SETUP_OPTIONS} == set(ProviderId)


def test_catalog_capabilities_use_only_the_shared_vocabulary() -> None:
    assert all(
        definition.capabilities.supported <= frozenset(Capability)
        for definition in PROVIDER_DEFINITIONS.values()
    )
    assert PROVIDER_DEFINITIONS[ProviderId.MOONRAKER].capabilities.supported == (
        frozenset(Capability)
    )


def test_catalog_document_is_json_compatible_and_stable() -> None:
    document = catalog_document()
    assert document["schemaVersion"] == 1
    assert list(document["providers"]) == [provider.value for provider in ProviderId]
    assert document["capabilities"] == [capability.value for capability in Capability]
    assert [option["value"] for option in document["setupOptions"]] == [
        option.kind for option in SETUP_OPTIONS
    ]


def test_secret_configuration_is_declared_without_product_field_names() -> None:
    for provider_id in (
        ProviderId.BAMBU_LAN,
        ProviderId.PRUSALINK,
        ProviderId.OCTOPRINT,
        ProviderId.ELEGOO_CENTAURI,
    ):
        assert any(
            field.value_kind == "secret"
            for field in PROVIDER_DEFINITIONS[provider_id].config_fields
        )
